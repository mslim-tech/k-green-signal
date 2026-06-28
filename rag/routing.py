# rag/routing.py
# -----------------------------------------------------------------------------
# 질문 → 표(std_id) 토픽 라우팅
#
# 이 파일의 역할:
#   - 서로 거의 똑같이 생긴 표가 인덱스에 공존하면(예: '친환경제품 확대 희망' vs
#     '환경표지 인증제품 확대 희망'), 검색이 둘 다 가져와 LLM 답변이 표를 오락가락
#     고른다(비결정적). 이를 막기 위해, 질문이 '명확히' 한 표를 가리키면 그 표로
#     검색을 좁힌다(std_id 필터) → 한 표만 근거가 되어 답변이 결정적이 된다.
#
#   원칙: 규칙은 '결정적 키워드'만 본다(LLM 추측 없음). 신호가 모호하면 라우팅하지
#         않고(None) 평소 검색에 맡긴다. 비슷한 표 쌍이 새로 생기면 규칙만 추가한다.
#
# 검증: uv run python -m pytest tests/test_routing.py -q   (LLM 불필요·결정적)
# -----------------------------------------------------------------------------

from __future__ import annotations


# 위에서부터 첫 번째로 맞는 규칙을 쓴다(순서 중요 — 더 구체적인 표를 먼저 둔다).
# 각 규칙(모두 '질문 문자열에 부분일치'로 본다):
#   topic     : 이 키워드들이 모두 있어야 규칙이 발동(주제 한정).
#   any       : 이 중 하나라도 있으면 매치(구분 신호).
#   all       : 이 키워드들이 모두 있어야 매치.
#   without   : 이 중 하나라도 있으면 매치 안 함(반대 표로 새는 것 방지).
#   std_id    : 매치 시 검색을 좁힐 표.
ROUTING_RULES: list[dict] = [
    # '확대 희망 품목' 주제의 두 표 구분 -------------------------------------
    # 환경표지/녹색제품/인증제품을 콕 집으면 → 환경표지 인증제품 확대 희망 표.
    {
        "topic": ["확대"],
        "any": ["환경표지", "녹색제품", "인증제품", "인증 제품"],
        "std_id": "환경표지_확대희망품목",
    },
    # 위 신호 없이 '친환경제품'만 말하면 → 친환경제품(전체) 확대 희망 표(표 3-60).
    {
        "topic": ["확대"],
        "all": ["친환경제품"],
        "without": ["환경표지", "녹색제품", "인증제품"],
        "std_id": "친환경제품_확대희망품목",
    },
]


def route(query: str) -> str | None:
    """ 질문이 특정 표를 명확히 가리키면 그 std_id 를, 아니면 None 을 돌려준다. """
    q = query or ""
    for rule in ROUTING_RULES:
        if any(t not in q for t in rule.get("topic", [])):
            continue
        if rule.get("any") and not any(k in q for k in rule["any"]):
            continue
        if any(k not in q for k in rule.get("all", [])):
            continue
        if any(k in q for k in rule.get("without", [])):
            continue
        return rule["std_id"]
    return None
