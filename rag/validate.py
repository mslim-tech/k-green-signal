# rag/validate.py
# -----------------------------------------------------------------------------
# 데이터 준비 게이트 (Readiness Gate)
#
# 이 파일의 역할:
#   - 인덱싱(6.2) 전에 "이 데이터가 RAG 에 들어갈 만큼 제대로 됐는가"를 검사한다.
#   - 원칙: "추측은 데이터가 아니다" → 값이 비었거나(추출 실패), 비전 후보가
#     아직 사람 확정 안 됐거나, 고우선 검수가 안 끝났으면 인덱싱을 '차단'한다.
#   - 무엇이 덜 됐는지(개수 + 예시 + 어디서 고칠지)를 돌려줘서 UI/사람이 처리하게 한다.
#
#   차단(block) 검사:
#     1) empty_chunks       : 응답 값이 하나도 없는 문항(빈 청크)
#     2) blank_values       : 라벨은 있는데 값이 빈 행 (corrections 적용 후에도)
#     3) unconfirmed_vision : 비전 후보(vision_candidates) 중 사람이 미확정
#     4) unreviewed_high    : 검수 큐의 high 우선순위 중 미검수
#   경고(warn, 비차단):
#     - unreviewed_medium, rows_with_warning
#
# 실행(현재 데이터 상태 점검):
#   uv run python rag/validate.py
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from rag import chunking, corrections
    from rag.logging_setup import setup_logging
except ImportError:
    import chunking
    import corrections
    from logging_setup import setup_logging

log = logging.getLogger(__name__)

try:
    from rag.paths import OUTPUT_DIR
except ImportError:
    from paths import OUTPUT_DIR
REVIEW_QUEUE = OUTPUT_DIR / "review_queue.csv"
VISION_CANDIDATES = OUTPUT_DIR / "vision_candidates.csv"

MAX_ITEMS = 8   # 리포트에 보여줄 예시 개수


@dataclass
class Check:
    id: str
    label: str
    severity: str          # "block" | "warn"
    ok: bool
    count: int
    items: list[str] = field(default_factory=list)   # 예시(연도/문항/위치)
    fix_hint: str = ""


@dataclass
class ReadinessReport:
    ok: bool                       # 차단 항목이 하나도 없으면 True
    checks: list[Check]
    blocking: list[Check]
    summary: str


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def validate_ready(strict: bool = True) -> ReadinessReport:
    """ 인덱싱 준비 상태를 검사해 ReadinessReport 를 돌려준다. """
    checks: list[Check] = []

    # corrections 가 적용된 행/청크 (인덱스에 실제로 들어갈 모습)
    rows = chunking.load_rows()
    chunks = chunking.build_chunks(rows)

    # 1) 빈 청크
    empty = [c for c in chunks if c["metadata"].get("n_responses", 0) == 0]
    checks.append(Check(
        id="empty_chunks", label="값이 하나도 없는 문항(빈 청크)",
        severity="block", ok=(len(empty) == 0), count=len(empty),
        items=[f"{c['metadata']['year']} {c['metadata']['std_id']} — {c['metadata'].get('source','')} p.{c['metadata'].get('page','')}" for c in empty[:MAX_ITEMS]],
        fix_hint="추출/검수 단계에서 값을 채우거나(비전 후보 확정), 해당 문항을 제외하세요.",
    ))

    # 2) 라벨은 있는데 값이 빈 행
    blanks = []
    for r in rows:
        label = (r.get("std_response_label") or r.get("response_label") or "").strip()
        if label and _num(r.get("value")) is None:
            blanks.append(f"{r.get('year')} {r.get('std_id')} · {label}")
    checks.append(Check(
        id="blank_values", label="라벨은 있는데 값이 빈 행",
        severity="block", ok=(len(blanks) == 0), count=len(blanks),
        items=blanks[:MAX_ITEMS],
        fix_hint="검수 단계에서 원문을 보고 값을 확정(corrections)하거나 행을 정리하세요.",
    ))

    # 3) 미확정 비전 후보
    confirmed = {corrections.row_key(rec) for rec in corrections.load_corrections()}
    cand = _load_csv(VISION_CANDIDATES)
    unconf = []
    for r in cand:
        if (r.get("status") or "").strip() != "candidate":
            continue
        key = ((r.get("year") or "").strip(), (r.get("std_id") or "").strip(),
               (r.get("response_label") or "").strip())
        if key not in confirmed:
            unconf.append(f"{key[0]} {key[1]} · {key[2]} (비전 {r.get('vision_value')})")
    checks.append(Check(
        id="unconfirmed_vision", label="사람이 확정 안 한 비전 후보",
        severity="block", ok=(len(unconf) == 0), count=len(unconf),
        items=unconf[:MAX_ITEMS],
        fix_hint="검수 단계에서 비전 후보의 출처를 보고 값을 확정하세요.",
    ))

    # 4) 미검수 high 우선순위
    reviewed = corrections.reviewed_keys()
    queue = _load_csv(REVIEW_QUEUE)
    unrev_high, unrev_med = [], []
    for r in queue:
        if corrections.row_key(r) in reviewed:
            continue
        prio = (r.get("review_priority") or "").strip()
        tag = f"{r.get('year')} {r.get('std_id')} · {r.get('std_response_label')}"
        if prio == "high":
            unrev_high.append(tag)
        elif prio == "medium":
            unrev_med.append(tag)
    checks.append(Check(
        id="unreviewed_high", label="검수 안 끝난 high 우선순위 행",
        severity="block", ok=(len(unrev_high) == 0), count=len(unrev_high),
        items=unrev_high[:MAX_ITEMS],
        fix_hint="검수 단계에서 high 항목을 확인/수정해 마무리하세요.",
    ))

    # 경고(비차단): 미검수 medium
    checks.append(Check(
        id="unreviewed_medium", label="검수 안 끝난 medium 우선순위 행",
        severity="warn", ok=(len(unrev_med) == 0), count=len(unrev_med),
        items=unrev_med[:MAX_ITEMS],
        fix_hint="권장: 시간이 되면 검수. 인덱싱은 가능.",
    ))

    blocking = [c for c in checks if c.severity == "block" and not c.ok]
    ok = (len(blocking) == 0) if strict else True
    n_block = sum(c.count for c in blocking)
    summary = (
        f"준비 완료 — 차단 항목 없음 (청크 {len(chunks)}개)"
        if ok else
        f"인덱싱 차단 — {len(blocking)}종 / 총 {n_block}건 처리 필요"
    )
    log.info("validate_ready: ok=%s, blocking=%s", ok, [c.id for c in blocking])
    return ReadinessReport(ok=ok, checks=checks, blocking=blocking, summary=summary)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    setup_logging("validate")

    rep = validate_ready(strict=True)
    print("\n" + "=" * 64)
    print(f"데이터 준비 게이트: {'✅ 통과' if rep.ok else '⛔ 차단'} — {rep.summary}")
    print("=" * 64)
    for c in rep.checks:
        mark = "✅" if c.ok else ("⛔" if c.severity == "block" else "⚠️")
        print(f"{mark} [{c.severity}] {c.label}: {c.count}건")
        for it in c.items:
            print(f"     - {it}")
        if not c.ok and c.fix_hint:
            print(f"     ↳ {c.fix_hint}")
    print("=" * 64)


if __name__ == "__main__":
    main()
