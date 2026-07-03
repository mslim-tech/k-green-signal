# rag/transform/flags.py
# -----------------------------------------------------------------------------
# 4단계 4.3: 의심값 자동 플래그
#
# 이 파일의 역할:
#   - 4.1 산출(standardized_long.clean.csv)의 각 행을 검사해 "사람이 한 번 봐야 할"
#     의심값에 플래그를 붙인다. 값을 고치지는 않는다(고치는 건 4.4 검수/사람 몫).
#
#   세 가지 검사:
#     4.3.1 전년 대비 급변  → flag_jump
#           같은 (std_id, std_response_label)의 직전 연도 값과 비교해 |Δ| 가 크면 표시.
#     4.3.2 서술 정합성     → flag_mismatch
#           prev_year_note(예: "54.0%에서 59.1%로 5.1%p 상승함")가 실제 값 변화와
#           모순되면 표시. 노트가 자유서술이라 LLM 으로 판단한다(사용자 선택).
#     4.3.3 합계 검증        → flag_sum_violation
#           단일응답(multi_response=False) + unit='%' 문항은 보기 값의 합이 100 근처여야
#           한다. 한 (year, std_id) 그룹의 합이 100±오차를 벗어나면 그룹 전체에 표시.
#
#   산출(원본은 보존):
#     outputs/standardized_long.flagged.csv  - clean CSV + 플래그/근거 컬럼 추가
#
# 보안: API Key 는 .env 의 OPENAI_API_KEY 에서만 읽는다.
#
# 실행 방법(4.1 라벨 표준화가 끝난 뒤):
#   uv run python -m rag.transform.flags
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

from rag.ingest.extract import get_client
from rag.core.config import STANDARDIZE_MODEL as MODEL_NAME
from rag.core.logging_setup import setup_logging
from rag.core.paths import OUTPUT_DIR

logger = logging.getLogger("flags")

# 4.2 중복정리(dedup) 결과가 있으면 그걸, 없으면 4.1 결과(clean)를 입력으로 쓴다.
_DEDUP = OUTPUT_DIR / "standardized_long.dedup.csv"           # 4.2 산출 (있으면 우선)
_CLEAN = OUTPUT_DIR / "standardized_long.clean.csv"           # 4.1 산출
SOURCE_CSV = _DEDUP if _DEDUP.exists() else _CLEAN
FLAGGED_CSV = OUTPUT_DIR / "standardized_long.flagged.csv"     # 4.3 산출

# --- 조정 가능한 임계값 (여기만 고치면 전부 반영) ----------------------------
JUMP_PP = 20.0    # 4.3.1: 전년 대비 |Δ| 가 이 %p 이상이면 급변으로 본다
SUM_TOL = 5.0     # 4.3.3: 단일응답 그룹 값 합이 100±이 값을 벗어나면 위반


# 4.3.2 LLM 이 노트 1건마다 돌려줄 형식 (Structured Outputs, strict)
MISMATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "reason"],
    "properties": {
        # consistent: 노트가 실제 값 변화와 일치 / contradictory: 모순 / unverifiable: 판단 불가
        "verdict": {"type": "string", "enum": ["consistent", "contradictory", "unverifiable"]},
        "reason": {"type": "string"},  # 한 줄 근거(한국어)
    },
}

MISMATCH_SYSTEM = (
    "너는 설문 결과의 '서술'과 '실제 수치'가 맞는지 검증하는 도구다.\n"
    "서술(prev_year_note)은 '직전 연도(prev_year) → 보고 연도(report_year)' 의 변화를 설명한다.\n"
    "그 두 연도의 실제 응답값 표를 준다. 서술의 방향/수치가 실제 변화와 맞는지 판정해라.\n\n"
    "판정:\n"
    "- consistent: 서술의 방향(상승/하락)과 수치가, 내가 표에서 '확인할 수 있는' 값들과 맞는다.\n"
    "- contradictory: 표에서 두 연도 값이 다 보이는 항목인데, 그 값이 서술과 분명히 반대이거나\n"
    "  서술이 댄 수치와 명백히 다르다. (한 항목이라도 명백한 모순이면 contradictory)\n"
    "- unverifiable: 비교에 필요한 직전 연도 값이 표에 없거나, 서술이 수치 없는 일반론이라 확인 불가.\n\n"
    "중요:\n"
    "- 표에 값이 '없어서' 확인 못 하는 것은 모순이 아니라 unverifiable 이다. 없는 걸 모순으로 몰지 마라.\n"
    "- 서술의 일부만 확인 가능하고 그 부분이 맞으면 consistent, 명백히 어긋나야만 contradictory.\n"
    "reason 에는 한국어 한 줄로 근거를 적어라."
)


def num(x):
    """ 문자열 값을 float 로. 비거나 숫자가 아니면 None. """
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# -----------------------------------------------------------------------------
# 입력 읽기
# -----------------------------------------------------------------------------
def load_rows() -> list[dict]:
    if not SOURCE_CSV.exists():
        raise RuntimeError(
            f"{SOURCE_CSV} 가 없습니다. 먼저 rag/transform/refine.py 로 4.1 라벨 표준화를 실행하세요."
        )
    with open(SOURCE_CSV, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# -----------------------------------------------------------------------------
# 4.3.1 전년 대비 급변
# -----------------------------------------------------------------------------
def compute_yoy(rows: list[dict]) -> None:
    """
    각 행에 prev_value(직전 연도 같은 라벨 값), yoy_delta, flag_jump 를 채운다.
    같은 (std_id, std_response_label) 안에서 '현재 연도보다 작은 가장 가까운 연도'를 직전으로 본다.
    """
    # (std_id, 표준라벨) -> {연도(int): 값(float)}
    series: dict[tuple, dict[int, float]] = defaultdict(dict)
    for r in rows:
        label = (r.get("std_response_label") or "").strip()
        v = num(r.get("value"))
        y = num(r.get("year"))
        if label and v is not None and y is not None:
            series[(r.get("std_id"), label)][int(y)] = v

    for r in rows:
        r["prev_value"] = ""
        r["yoy_delta"] = ""
        r["flag_jump"] = ""
        label = (r.get("std_response_label") or "").strip()
        v = num(r.get("value"))
        y = num(r.get("year"))
        if not label or v is None or y is None:
            continue
        years = series[(r.get("std_id"), label)]
        prev_years = [yr for yr in years if yr < int(y)]
        if not prev_years:
            continue
        prev_y = max(prev_years)
        prev_v = years[prev_y]
        delta = round(v - prev_v, 1)
        r["prev_value"] = prev_v
        r["yoy_delta"] = delta
        r["flag_jump"] = "True" if abs(delta) >= JUMP_PP else ""


# -----------------------------------------------------------------------------
# 4.3.3 합계 검증 (단일응답 + unit='%')
# -----------------------------------------------------------------------------
def compute_sum_check(rows: list[dict]) -> None:
    """ (year, std_id) 그룹의 값 합이 100±SUM_TOL 밖이면 그룹 전체에 flag_sum_violation. """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("multi_response") == "False" and r.get("unit") == "%" and num(r.get("value")) is not None:
            groups[(r.get("year"), r.get("std_id"))].append(r)

    # 기본값 비움
    for r in rows:
        r["sum_total"] = ""
        r["flag_sum_violation"] = ""

    for members in groups.values():
        total = round(sum(num(m["value"]) for m in members), 1)
        violation = abs(total - 100.0) > SUM_TOL
        for m in members:
            m["sum_total"] = total
            m["flag_sum_violation"] = "True" if violation else ""


# -----------------------------------------------------------------------------
# 4.3.2 서술 정합성 (LLM)
# -----------------------------------------------------------------------------
def _call_mismatch(client, std_id: str, note: str, table: list[dict],
                   report_year: str, prev_year: str, retries: int = 2) -> dict:
    user_prompt = (
        f"[문항] {std_id}\n"
        f"[report_year] {report_year}  [prev_year] {prev_year}\n\n"
        "[직전·보고 연도 실제 응답값]\n"
        + json.dumps(table, ensure_ascii=False, indent=2)
        + "\n\n[검증할 서술(prev_year_note)]\n" + note
    )
    last_error = None
    for _ in range(retries):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                temperature=0,
                messages=[
                    {"role": "system", "content": MISMATCH_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "note_consistency",
                        "strict": True,
                        "schema": MISMATCH_SCHEMA,
                    },
                },
            )
            return json.loads(response.choices[0].message.content)
        except Exception as error:
            last_error = error
    print(f"  ⚠️ '{std_id}' 서술 검증 실패: {last_error}")
    logger.warning("서술 정합성 LLM 호출 실패 — std_id=%s: %s", std_id, last_error)
    return {"verdict": "unverifiable", "reason": f"호출 실패: {last_error}"}


def compute_mismatch(client, rows: list[dict]) -> None:
    """
    prev_year_note 가 있는 (std_id, year) 그룹마다 LLM 으로 정합성 판정.
    contradictory 면 그 그룹 전체에 flag_mismatch=True. 근거/판정도 컬럼으로 남긴다.
    """
    for r in rows:
        r["flag_mismatch"] = ""
        r["mismatch_verdict"] = ""
        r["mismatch_reason"] = ""

    # 노트가 달린 (std_id, year) 그룹 모으기
    note_groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if (r.get("prev_year_note") or "").strip():
            note_groups[(r.get("std_id"), r.get("year"))].append(r)

    # (std_id, 연도) -> {표준라벨: 값}  (직전 연도 값 조회용)
    value_by: dict[tuple, dict[str, float]] = defaultdict(dict)
    for r in rows:
        label = (r.get("std_response_label") or "").strip()
        v = num(r.get("value"))
        if label and v is not None:
            value_by[(r.get("std_id"), str(r.get("year")))][label] = v

    def set_group(members, verdict, reason):
        is_mismatch = verdict == "contradictory"
        for m in members:
            m["flag_mismatch"] = "True" if is_mismatch else ""
            m["mismatch_verdict"] = verdict
            m["mismatch_reason"] = reason
        return is_mismatch

    total = len(note_groups)
    for i, ((std_id, year), members) in enumerate(note_groups.items(), start=1):
        note = (members[0].get("prev_year_note") or "").strip()
        report_year = str(year)
        prev_year = str(int(float(report_year)) - 1)

        cur_vals = value_by.get((std_id, report_year), {})
        prev_vals = value_by.get((std_id, prev_year), {})

        # 직전 연도 값이 하나도 없으면(예: 2023 미보유) 비교 불가 → 호출 없이 unverifiable.
        if not prev_vals:
            set_group(members, "unverifiable", f"직전 연도({prev_year}) 값이 데이터에 없어 검증 불가")
            print(f"  [{i}/{total}] {std_id} ({year}): unverifiable (직전연도 부재, 호출 생략)")
            continue

        labels = sorted(set(cur_vals) | set(prev_vals))
        table = [{
            "label": lab,
            f"value_{prev_year}": prev_vals.get(lab),
            f"value_{report_year}": cur_vals.get(lab),
        } for lab in labels]

        result = _call_mismatch(client, std_id, note, table, report_year, prev_year)
        verdict = result.get("verdict", "unverifiable")
        is_mismatch = set_group(members, verdict, result.get("reason", ""))
        mark = "⚠️ 모순" if is_mismatch else verdict
        print(f"  [{i}/{total}] {std_id} ({year}): {mark}")


# -----------------------------------------------------------------------------
# 저장
# -----------------------------------------------------------------------------
NEW_COLUMNS = [
    "prev_value", "yoy_delta", "flag_jump",
    "flag_mismatch", "mismatch_verdict", "mismatch_reason",
    "sum_total", "flag_sum_violation",
]


def save_flagged(rows: list[dict]) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    base_cols = [c for c in (rows[0].keys() if rows else []) if c not in NEW_COLUMNS]
    out_cols = base_cols + NEW_COLUMNS
    with open(FLAGGED_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in out_cols})
    return FLAGGED_CSV


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    setup_logging("flags")   # 구조화 로그(시작·집계·에러)를 run 로그·파일에 남긴다.

    rows = load_rows()
    print(f"\n의심값 자동 플래그: {len(rows)}행 검사 (점프≥{JUMP_PP}%p, 합계 100±{SUM_TOL})\n")
    logger.info("flags 시작 — 검사 %d행 · 점프≥%s%%p · 합계 100±%s · 모델 %s",
                len(rows), JUMP_PP, SUM_TOL, MODEL_NAME)

    print("4.3.1 전년 대비 급변...")
    compute_yoy(rows)
    print("4.3.3 합계 검증...")
    compute_sum_check(rows)
    print(f"4.3.2 서술 정합성 (LLM, {MODEL_NAME})...")
    client = get_client()
    compute_mismatch(client, rows)

    path = save_flagged(rows)

    n_jump = sum(1 for r in rows if r["flag_jump"] == "True")
    n_mis = sum(1 for r in rows if r["flag_mismatch"] == "True")
    n_sum = sum(1 for r in rows if r["flag_sum_violation"] == "True")
    flagged_rows = sum(1 for r in rows
                       if r["flag_jump"] == "True" or r["flag_mismatch"] == "True"
                       or r["flag_sum_violation"] == "True")

    print("\n" + "=" * 60)
    print(f"플래그 완료 — 표시된 행 {flagged_rows}개")
    print(f"  flag_jump(급변)          : {n_jump}행")
    print(f"  flag_mismatch(서술 모순)  : {n_mis}행")
    print(f"  flag_sum_violation(합계)  : {n_sum}행")
    print(f"💾 {path}  (원본 {SOURCE_CSV.name} 은 보존)")
    print("=" * 60)
    logger.info("flags 완료 — 표시행 %d개(jump %d · mismatch %d · sum %d) · %s",
                flagged_rows, n_jump, n_mis, n_sum, path.name)


if __name__ == "__main__":
    main()
