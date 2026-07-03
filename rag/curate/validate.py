# rag/curate/validate.py
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
#     5) ungated_uncertain  : 검수 큐를 거치지 않은 불확실 행(비전 등 side-channel 직접 기입)
#   경고(warn, 비차단):
#     - unreviewed_medium, rows_with_warning
#
# 실행(현재 데이터 상태 점검):
#   uv run python -m rag.curate.validate
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rag.retrieval import chunking
from rag.curate import corrections
from rag.core.logging_setup import setup_logging
from rag.core.paths import OUTPUT_DIR

logger = logging.getLogger(__name__)

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


def is_uncertain_high(row: dict, reviewed: set) -> bool:
    """ 인덱싱을 막는 '불확실 high' 판정 — 게이트·사이드바·집중필터·adjudicate 의 단일 소스.
        미검수 high 중 저신뢰·빈칸/비숫자·합계이상만 True. 고신뢰·숫자·합계정상은
        분석 플래그(급변/노트모순)만 있는 충실 추출이므로 인덱싱 허용(=False). """
    if row.get("review_priority") != "high":
        return False
    if corrections.row_key(row) in reviewed:
        return False
    conf = (row.get("extraction_confidence") or "").strip().lower()
    is_num = _num(row.get("value")) is not None
    sum_bad = row.get("flag_sum_violation") == "True"
    return not (conf == "high" and is_num and not sum_bad)


def validate_ready(strict: bool = True) -> ReadinessReport:
    """ 인덱싱 준비 상태를 검사해 ReadinessReport 를 돌려준다. """
    checks: list[Check] = []

    # corrections 가 적용된 행/청크 (인덱스에 실제로 들어갈 모습)
    rows = chunking.load_rows()
    chunks = chunking.build_chunks(rows)

    # 1) 빈 청크
    empty = [c for c in chunks if c["metadata"].get("n_responses", 0) == 0]
    checks.append(Check(
        id="empty_chunks", label="값이 하나도 없는 문항(빈 청크 — 색인 제외)",
        severity="warn", ok=(len(empty) == 0), count=len(empty),
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
        id="blank_values", label="라벨은 있는데 값이 빈 행(값 없음 — 색인 제외)",
        severity="warn", ok=(len(blanks) == 0), count=len(blanks),
        items=blanks[:MAX_ITEMS],
        fix_hint="값이 없어 색인에서 자동 제외됩니다. 채우려면 검수/비전으로 확정하세요(선택).",
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
        id="unconfirmed_vision", label="사람이 확정 안 한 비전 후보(제안일 뿐 — 색인 안 됨)",
        severity="warn", ok=(len(unconf) == 0), count=len(unconf),
        items=unconf[:MAX_ITEMS],
        fix_hint="비전 후보는 확정 전까지 색인되지 않습니다. 확정하면 데이터로 반영(선택).",
    ))

    # 4) 미검수 high 우선순위 — 게이트 완화(하이브리드)
    #    high-confidence·숫자·합계정상 행은 '분석 플래그(급변/노트모순)'만 붙은 충실 추출이므로
    #    인덱싱을 막지 않는다(플래그는 경고로 보존). 진짜 불확실(빈칸·저신뢰·합계이상)만 차단해
    #    LLM 검증(Stage 2)·사람 검수로 보낸다.
    reviewed = corrections.reviewed_keys()
    queue = _load_csv(REVIEW_QUEUE)
    unrev_high, relaxed_high, unrev_med = [], [], []
    for r in queue:
        if corrections.row_key(r) in reviewed:
            continue
        prio = (r.get("review_priority") or "").strip()
        tag = f"{r.get('year')} {r.get('std_id')} · {r.get('std_response_label')}"
        if prio == "high":
            if is_uncertain_high(r, reviewed):
                unrev_high.append(tag)     # 빈칸·저신뢰·합계이상 → 차단
            else:
                relaxed_high.append(tag)   # 충실 추출 + 분석 플래그만 → 인덱싱 허용
        elif prio == "medium":
            unrev_med.append(tag)
    checks.append(Check(
        id="unreviewed_high", label="검수 안 끝난 high 행(불확실 — 빈칸·저신뢰·합계이상)",
        severity="block", ok=(len(unrev_high) == 0), count=len(unrev_high),
        items=unrev_high[:MAX_ITEMS],
        fix_hint="빈칸·저신뢰·합계이상 행입니다. LLM 검증 또는 검수로 확정하세요.",
    ))
    # 완화로 인덱싱 허용된 high(분석 플래그만) — 경고(비차단)로 가시화한다.
    checks.append(Check(
        id="flagged_high_indexed", label="분석 플래그만 있는 high(충실 추출 — 인덱싱 허용)",
        severity="warn", ok=(len(relaxed_high) == 0), count=len(relaxed_high),
        items=relaxed_high[:MAX_ITEMS],
        fix_hint="급변/노트모순 등 분석 경고만 있는 고신뢰 추출입니다. 인덱싱되며, 필요 시 검토하세요.",
    ))

    # 5) 검수 큐를 '거치지 않은' 불확실 인덱싱 행 (integrate_oldyears 등 side-channel 로
    #    clean/dedup.csv 에 직접 기입돼 flags→review 를 건너뛴 비전/저신뢰 값).
    #    "추측은 데이터가 아니다" → 값이 있어도 사람 확정 전이면 인덱싱 차단.
    #    (정상 흐름의 저신뢰 행은 review_queue 에 등재돼 검사 4/큐가 담당하므로 여기서 제외.)
    queued_keys = {corrections.row_key(r) for r in queue}
    ungated = []
    for r in rows:
        if _num(r.get("value")) is None:
            continue  # 값이 빈 행은 blank_values(검사 2)가 담당
        conf = (r.get("extraction_confidence") or "").strip().lower()
        warn = (r.get("warning") or "").strip()
        if not warn and conf not in ("low", "medium"):
            continue  # 고신뢰·무경고 → 확정 사실로 취급
        key = corrections.row_key(r)
        if key in reviewed or key in confirmed or key in queued_keys:
            continue  # 사람이 검수/확정했거나 정상적으로 검수 큐에 등재됨
        ungated.append(f"{r.get('year')} {r.get('std_id')} · {r.get('std_response_label')} ({warn or conf})")
    checks.append(Check(
        id="ungated_uncertain", label="검수 큐를 거치지 않은 불확실 행(비전 등 side-channel)",
        severity="block", ok=(len(ungated) == 0), count=len(ungated),
        items=ungated[:MAX_ITEMS],
        fix_hint="integrate 등으로 직접 기입된 저신뢰/비전 값입니다. flags→review→검수(confirm)로 확정하세요.",
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
    logger.info("validate_ready: ok=%s, blocking=%s", ok, [c.id for c in blocking])
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
