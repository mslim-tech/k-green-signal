# rag/transform/review.py
# -----------------------------------------------------------------------------
# 4단계 4.4: 저신뢰 검수 큐 만들기
#
# 이 파일의 역할:
#   - 4.3 산출(standardized_long.flagged.csv)에서 "사람이 한 번 확인해야 할" 행만 골라
#     검수용 목록(review_queue.csv)으로 뽑는다. 값을 고치지는 않는다(고치는 건 5단계 UI).
#
#   큐에 담는 기준(하나라도 해당하면 포함):
#     - extraction_confidence ∈ {low, medium}   (추출 자체가 불확실)
#     - warning 이 비어있지 않음                  (추출 단계가 남긴 경고)
#     - 4.3 플래그: flag_jump / flag_mismatch / flag_sum_violation
#     - 중복키: 같은 (year, std_id, std_response_label) 가 2번 이상 등장
#               (예: 2025 '대형마트 등 유통매장 안내' 가 추출 중복으로 두 번)
#
#   각 행에 왜 뽑혔는지(review_reasons)와 우선순위(review_priority)를 달고,
#   원문을 찾아갈 수 있도록 출처/페이지/그림캡션을 함께 남긴다.
#
#   산출:
#     outputs/review_queue.csv  - 검수 대상 행 + review_reasons / review_priority
#
# 실행 방법(4.3 플래그가 끝난 뒤):
#   uv run python -m rag.transform.review
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import logging
import sys
from collections import defaultdict


from rag.core.paths import OUTPUT_DIR
from rag.core.logging_setup import setup_logging
logger = logging.getLogger("review")
SOURCE_CSV = OUTPUT_DIR / "standardized_long.flagged.csv"   # 4.3 산출 (입력, 보존)
QUEUE_CSV = OUTPUT_DIR / "review_queue.csv"                 # 4.4 산출

# 우선순위가 high 인 사유 (데이터 값 자체가 틀렸을 가능성이 큰 것)
HIGH_REASONS = {"low_confidence", "flag_mismatch", "duplicate"}


def load_rows() -> list[dict]:
    if not SOURCE_CSV.exists():
        raise RuntimeError(
            f"{SOURCE_CSV} 가 없습니다. 먼저 rag/transform/flags.py 로 4.3 의심값 플래그를 실행하세요."
        )
    with open(SOURCE_CSV, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def find_duplicate_rows(rows: list[dict]) -> set[int]:
    """ 같은 (year, std_id, std_response_label) 가 2번 이상인 행들의 인덱스 집합. """
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        label = (r.get("std_response_label") or "").strip()
        if label:
            groups[(r.get("year"), r.get("std_id"), label)].append(i)
    dup_idx: set[int] = set()
    for idxs in groups.values():
        if len(idxs) > 1:
            dup_idx.update(idxs)
    return dup_idx


def reasons_for(r: dict, is_dup: bool) -> list[str]:
    """ 이 행이 검수 대상인 이유 목록. 없으면 빈 리스트(=큐에 안 들어감). """
    reasons: list[str] = []
    conf = (r.get("extraction_confidence") or "").strip()
    if conf == "low":
        reasons.append("low_confidence")
    elif conf == "medium":
        reasons.append("medium_confidence")
    if (r.get("warning") or "").strip():
        reasons.append("warning")
    if r.get("flag_jump") == "True":
        reasons.append("flag_jump")
    if r.get("flag_mismatch") == "True":
        reasons.append("flag_mismatch")
    if r.get("flag_sum_violation") == "True":
        reasons.append("flag_sum_violation")
    if is_dup:
        reasons.append("duplicate")
    return reasons


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    setup_logging("review")   # 구조화 로그(시작·집계)를 run 로그·파일에 남긴다.

    rows = load_rows()
    dup_idx = find_duplicate_rows(rows)
    logger.info("review 시작 — 입력 %d행 · 중복키 %d행", len(rows), len(dup_idx))

    # 검수 대상만 추리고 사유/우선순위 부여
    queue: list[dict] = []
    for i, r in enumerate(rows):
        reasons = reasons_for(r, i in dup_idx)
        if not reasons:
            continue
        priority = "high" if any(x in HIGH_REASONS for x in reasons) else "medium"
        # 출처를 한눈에 찾도록 사람이 읽는 위치 문자열도 만든다.
        pages = r.get("page_start", "")
        if r.get("page_end") and r.get("page_end") != r.get("page_start"):
            pages = f"{r.get('page_start')}-{r.get('page_end')}"
        locator = f"{r.get('source', '')} p.{pages}".strip()
        queue.append({
            "review_priority": priority,
            "review_reasons": "; ".join(reasons),
            "source_locator": locator,
            **r,
        })

    # high 가 위로, 그 안에서는 연도·문항 순으로 정렬
    queue.sort(key=lambda q: (0 if q["review_priority"] == "high" else 1,
                              str(q.get("year")), str(q.get("std_id"))))

    # 컬럼: 검수용 메타를 앞에 두고 나머지 원본 컬럼을 잇는다(손실 없음).
    lead = ["review_priority", "review_reasons", "source_locator"]
    base_cols = list(rows[0].keys()) if rows else []
    out_cols = lead + base_cols

    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(QUEUE_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_cols)
        writer.writeheader()
        for q in queue:
            writer.writerow({c: q.get(c, "") for c in out_cols})

    # 요약
    n_high = sum(1 for q in queue if q["review_priority"] == "high")
    reason_count: dict[str, int] = defaultdict(int)
    for q in queue:
        for x in q["review_reasons"].split("; "):
            reason_count[x] += 1

    print("\n" + "=" * 60)
    print(f"검수 큐: {len(queue)}행 (전체 {len(rows)}행 중) — high {n_high} / medium {len(queue) - n_high}")
    print("사유별 행수:")
    for reason, n in sorted(reason_count.items(), key=lambda x: -x[1]):
        print(f"  - {reason}: {n}")
    print(f"💾 {QUEUE_CSV}  (입력 {SOURCE_CSV.name} 은 보존)")
    print("=" * 60)
    logger.info("review 완료 — 검수 큐 %d행(high %d · medium %d) / 전체 %d행 · %s",
                len(queue), n_high, len(queue) - n_high, len(rows), QUEUE_CSV.name)


if __name__ == "__main__":
    main()
