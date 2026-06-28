# rag/dedup.py
# -----------------------------------------------------------------------------
# 4단계 4.2: 중복 제거 / 잘못 합쳐진 항목 분리
#
# 이 파일의 역할:
#   4.1 산출(standardized_long.clean.csv)에서 같은 (year, std_id, std_response_label)
#   가 2번 이상 나오는 "가짜 중복"을 세 종류로 나눠 정리한다. 값을 새로 만들지는 않는다.
#
#   A. 문항 과잉병합 (다른 페이지의 다른 질문이 한 std_id로):
#      예) '환경표지(에코라벨)' 질문과 '환경성적표지(EPD)' 질문이 한 std_id로 묶임.
#      → subsection 키워드로 알아내 별도 std_id 로 분리한다 (SPLIT_RULES).
#
#   B. 라벨 과잉병합 (4.1 이 서로 다른 보기를 한 라벨로):
#      같은 블록(같은 페이지) 안에서 한 std_response_label 에 '서로 다른 값'이 여럿.
#      예) 명칭선호에서 '친환경표지(마크)'=55.6, '환경마크'=19.3 ... 이 한 라벨로 합쳐짐.
#      → 원래 응답 라벨(response_label)로 되돌려 분리한다(un-merge).
#
#   C. 진짜 중복 (같은 보기가 두 번, 값이 같거나 한쪽이 빈칸):
#      예) '대형마트 등 유통매장 안내'(30.6) + '대형마트 등 유통매장'(30.6) — 추출이 두 번 뽑음.
#      → 값 있는 행 하나만 남기고 나머지 행을 제거한다.
#
#   B/C 구분 기준: 같은 블록의 중복 행들의 값이 '서로 다르면 B(분리)', '같거나 한쪽뿐이면 C(제거)'.
#
#   산출(원본 보존):
#     outputs/standardized_long.dedup.csv  - 정리된 데이터셋 (4.3 이 이걸 입력으로 씀)
#     outputs/dedup_log.csv                 - 무엇을 분리/제거했는지 내역
#
# 실행 방법(4.1 라벨 표준화가 끝난 뒤):
#   uv run python rag/dedup.py
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path


try:
    from rag.paths import OUTPUT_DIR
except ImportError:
    from paths import OUTPUT_DIR
SOURCE_CSV = OUTPUT_DIR / "standardized_long.clean.csv"     # 4.1 산출 (입력, 보존)
DEDUP_CSV = OUTPUT_DIR / "standardized_long.dedup.csv"      # 4.2 산출
LOG_CSV = OUTPUT_DIR / "dedup_log.csv"

# --- A. 문항 분리 규칙 -------------------------------------------------------
# over-merged std_id : [(키워드, 새 std_id, 새 std_label), ...]
#   블록의 section/subsection 글자에 키워드가 들어 있으면 그 새 std_id 로 보낸다.
#   (안 맞으면 원래 std_id 유지) — 같은 std_id 라도 연도/페이지마다 다른 문항이면 이렇게 갈라낸다.
SPLIT_RULES: dict[str, list[tuple[str, str, str]]] = {
    "환경성적표지_구매유도요인": [
        ("환경표지 인증제품", "환경표지_구매유도요인", "환경표지 인증제품 구매 유도 요인"),
    ],
    "환경성적표지_우선구매이유": [
        ("환경표지 인증제품", "환경표지_우선구매이유", "환경표지 인증제품 구매 이유"),
    ],
    "친환경고려_제품": [
        ("포인트 적립", "친환경소비_포인트적립_희망품목", "포인트 적립 희망 친환경 소비 품목"),
    ],
    # 2023 은 '확대 희망 친환경제품'(표 3-60)이지만, 2024·2025 의 같은 std_id 는
    # '그린카드 포인트 적립 희망 품목'(다른 문항)으로 잘못 병합됨. 사용자 확인 결과
    # 그 문항은 2023 의 '친환경소비_포인트적립_희망품목' 과 같은 문항 → 그쪽으로 합류시킨다.
    # (구분자가 subsection 이 아니라 section 글자에 있어 section 까지 본다.)
    "친환경제품_확대희망품목": [
        ("그린카드 포인트 적립", "친환경소비_포인트적립_희망품목", "포인트 적립 희망 친환경 소비 품목"),
    ],
}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def block_key(r: dict) -> tuple:
    """ 같은 원본 블록(같은 질문)인지 식별. 같은 페이지·소절이면 같은 블록으로 본다. """
    return (r.get("year"), r.get("source"), r.get("page_start"),
            r.get("page_end"), r.get("subsection"))


def load_rows() -> list[dict]:
    if not SOURCE_CSV.exists():
        raise RuntimeError(
            f"{SOURCE_CSV} 가 없습니다. 먼저 rag/refine.py 로 4.1 라벨 표준화를 실행하세요."
        )
    with open(SOURCE_CSV, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    rows = load_rows()
    log: list[dict] = []

    def add_log(action, r, detail):
        log.append({
            "action": action,
            "year": r.get("year"),
            "std_id": r.get("std_id"),
            "response_label": r.get("response_label"),
            "std_response_label": r.get("std_response_label"),
            "value": r.get("value"),
            "page_start": r.get("page_start"),
            "detail": detail,
        })

    # --- A. 문항 과잉병합 분리 (블록 단위로 std_id 재배정) ---------------------
    n_split = 0
    for r in rows:
        rules = SPLIT_RULES.get(r.get("std_id"))
        if not rules:
            continue
        # section 과 subsection 을 합친 글자에서 키워드를 찾는다(구분자가 둘 중 어디 있든 잡히게).
        text = (r.get("section") or "") + " " + (r.get("subsection") or "")
        for keyword, new_id, new_label in rules:
            if keyword in text:
                add_log("split", r, f"{r['std_id']} → {new_id} (키워드 '{keyword}')")
                r["std_id"] = new_id
                r["std_label"] = new_label
                n_split += 1
                break

    # --- B/C. 같은 블록 내 중복 처리 -----------------------------------------
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if (r.get("std_response_label") or "").strip():
            groups[(r.get("year"), r.get("std_id"), r.get("std_response_label"))].append(i)

    drop_idx: set[int] = set()
    n_unmerge = 0
    n_drop = 0
    n_unresolved = 0

    for key, idxs in groups.items():
        if len(idxs) <= 1:
            continue
        grp = [rows[i] for i in idxs]
        # A 로 분리되지 않고 남은 '다른 블록' 중복이면 사람이 봐야 함(여기선 그대로 두고 기록만).
        if len({block_key(r) for r in grp}) > 1:
            for i in idxs:
                add_log("unresolved_cross_block", rows[i], "다른 블록 중복 — SPLIT_RULES 추가 검토 필요")
            n_unresolved += 1
            continue

        nonempty = [num(rows[i].get("value")) for i in idxs]
        nonempty = [v for v in nonempty if v is not None]

        if len(set(nonempty)) <= 1:
            # C. 진짜 중복 → 값 있는 행 하나만 남기고 제거
            keeper = next((i for i in idxs if num(rows[i].get("value")) is not None), idxs[0])
            for i in idxs:
                if i != keeper:
                    drop_idx.add(i)
                    add_log("drop_duplicate", rows[i], f"진짜 중복 — 유지행 값={rows[keeper].get('value')}")
                    n_drop += 1
        else:
            # B. 라벨 과잉병합 → 원래 응답 라벨로 분리(un-merge)
            for i in idxs:
                r = rows[i]
                old_std = r["std_response_label"]
                if r.get("response_label"):
                    add_log("unmerge_label", r, f"std_response_label '{old_std}' → '{r['response_label']}' 로 분리")
                    r["std_response_label"] = r["response_label"]
                    n_unmerge += 1

    # --- 저장 -----------------------------------------------------------------
    out_rows = [r for i, r in enumerate(rows) if i not in drop_idx]
    OUTPUT_DIR.mkdir(exist_ok=True)
    fieldnames = list(out_rows[0].keys()) if out_rows else []
    with open(DEDUP_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    log_cols = ["action", "year", "std_id", "response_label", "std_response_label",
                "value", "page_start", "detail"]
    with open(LOG_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=log_cols)
        writer.writeheader()
        writer.writerows(log)

    print("\n" + "=" * 60)
    print(f"4.2 중복 제거/분리 완료 — {len(rows)}행 → {len(out_rows)}행")
    print(f"  A 문항 분리(split)        : {n_split}행")
    print(f"  B 라벨 분리(un-merge)     : {n_unmerge}행")
    print(f"  C 진짜 중복 제거(drop)    : {n_drop}행")
    if n_unresolved:
        print(f"  ⚠️ 미해결 다른블록 중복   : {n_unresolved}그룹 (dedup_log 확인)")
    print(f"💾 데이터 : {DEDUP_CSV}  (원본 {SOURCE_CSV.name} 보존)")
    print(f"💾 내역   : {LOG_CSV}")
    print("=" * 60)


if __name__ == "__main__":
    main()
