# rag/std_aliases.py
# -----------------------------------------------------------------------------
# 문항(std_id) 표준화 별칭 — 연도 간 '같은 문항이 다른 이름'으로 쪼개진 것을 통합
#
# 이 파일의 역할:
#   - 설문이 해마다 문항 표현을 바꾸면 같은 질문이 연도별로 다른 std_id 로
#     표준화돼 시계열이 끊긴다. 사람(도메인 전문가)이 "같은 문항"으로 확정한
#     쌍만 여기 별칭으로 적어 하나의 canonical std_id 로 합친다.
#   - corrections 와 같은 원칙: 자동(유사도)으로 합치지 않는다. 여기 표는
#     전부 사람이 확정한 것만 담는다("추측은 데이터가 아니다").
#
#   적용 지점: chunking.load_rows() 마지막(=corrections·skip·복원 이후). 그래서
#             인덱스(Q&A)와 신호등이 같은 통합 결과를 본다.
#
# 검증: uv run python -m pytest tests/test_std_aliases.py -q   (LLM 불필요·결정적)
#
# ── 확정 이력(2026-06-28, 사용자 mslim) ────────────────────────────────────────
#  #1 환경표지_구매이유(2023) → 환경표지_우선구매이유(2024·2025): 같은 문항.
#       ⚠️ 단, 2023은 단일응답(합 ~91%)·2024·25는 복수응답(합 300%+)이라 값은
#          추세 비교 불가 → std_id(문항 정체성)만 통합하고 응답 라벨은 정렬하지
#          않는다(합치면 단일↔복수 가짜 추세가 생김). Q&A 그룹핑용 통합.
#  #2 환경표지_재구매의향(2023) → 환경표지_우선구매의향(2025): 같은 문항.
#       단일 비율("의향 있음" 96.0% ↔ "구매 의향 있음" 93.8%)이라 라벨까지
#       정렬해 시계열 연결(2개년; 2024 미조사라 3개년은 아님).
# -----------------------------------------------------------------------------

from __future__ import annotations

# old std_id → canonical std_id (사람 확정 쌍만)
STD_ID_ALIASES: dict[str, str] = {
    "환경표지_구매이유": "환경표지_우선구매이유",   # #1
    "환경표지_재구매의향": "환경표지_우선구매의향",  # #2
}

# canonical std_id → 통합 후 보여줄 std_label(있으면 그 라벨로 통일)
STD_LABEL_CANON: dict[str, str] = {
    "환경표지_우선구매이유": "환경표지 인증제품 우선 구매 이유",
    "환경표지_우선구매의향": "환경표지 인증제품 우선 구매 의향",
}

# canonical std_id → {옛 응답라벨: 통일 응답라벨} (값 비교 가능한 단일응답만 정렬)
RESPONSE_LABEL_ALIASES: dict[str, dict[str, str]] = {
    "환경표지_우선구매의향": {"의향 있음": "구매 의향 있음"},   # #2
}


def apply_aliases(rows: list[dict]) -> list[dict]:
    """ 행들의 std_id/std_label/std_response_label 을 확정 별칭으로 통일해 돌려준다.
        원본 리스트는 건드리지 않고 새 dict 리스트를 만든다(부수효과 최소화). """
    if not (STD_ID_ALIASES or STD_LABEL_CANON or RESPONSE_LABEL_ALIASES):
        return rows
    out: list[dict] = []
    for r in rows:
        sid = (r.get("std_id") or "").strip()
        canon = STD_ID_ALIASES.get(sid, sid)
        if canon == sid and canon not in STD_LABEL_CANON and canon not in RESPONSE_LABEL_ALIASES:
            out.append(r)                       # 손댈 게 없으면 그대로
            continue
        nr = dict(r)
        nr["std_id"] = canon
        if canon in STD_LABEL_CANON:
            nr["std_label"] = STD_LABEL_CANON[canon]
        lbl_map = RESPONSE_LABEL_ALIASES.get(canon)
        if lbl_map:
            cur = (nr.get("std_response_label") or "").strip()
            if cur in lbl_map:
                nr["std_response_label"] = lbl_map[cur]
        out.append(nr)
    return out
