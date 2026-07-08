# rag/retrieval/answer.py
# -----------------------------------------------------------------------------
# 6.6 근거 인용 답변 (Grounded Answer)
#
# 이 파일의 역할:
#   - 사용자 질문 → retriever 로 관련 청크 top-k 검색 → 그 청크'만' 근거로
#     ANSWER_MODEL 이 답하게 한다. 모든 수치/주장에 출처(source p.N)를 인용하고,
#     검색 결과에 없으면 "문서에서 찾을 수 없습니다" 라고 답한다(환각 차단).
#
# 보안: API Key 는 .env 에서만.
#
# 실행(단독 테스트):
#   uv run python -m rag.retrieval.answer "2024년 환경표지 정의 인지율은?"
# -----------------------------------------------------------------------------

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field

from rag.ingest.extract import get_client
from rag.core.config import ANSWER_MODEL, REWRITE_MODEL, EXAMPLE_Q_MODEL
from rag.retrieval.retriever import search, Hit
from rag.retrieval.routing import route
SYSTEM_PROMPT = (
    "너는 '친환경 소비 인지도 조사' 정형 데이터에 근거해 답하는 도우미다.\n"
    "규칙(반드시 지켜라):\n"
    "1. 아래 [근거]로 준 자료 안에 있는 내용만으로 답한다. 자료에 없으면 "
    "'문서에서 찾을 수 없습니다'라고만 답한다. 절대 추측하거나 지어내지 마라.\n"
    "2. 수치·사실을 말할 때마다 바로 뒤에 출처를 붙인다. [근거] 자료 머리에 적힌 "
    "'실제 파일명'을 그대로 써라(예: [출처: 2025년 친환경생활·소비 국민 인지도 조사 결과보고서.pdf p.33]). "
    "'파일' 같은 자리표시자를 그대로 쓰지 마라.\n"
    "3. 연도가 여러 개면 연도를 구분해서 답한다.\n"
    "4. 순위(1위·가장 많은·최다 등)를 물으면, 먼저 보기 목록에서 '기타', '없음', '모름', "
    "'무응답', '없음/모름/무응답', '소계', '합계', '전체' 같은 **집계·기타·비응답 항목을 "
    "모두 제외**하라. 그렇게 남은 '실제 응답 항목(품목/보기)' 중에서만 1·2·3위를 정한다. "
    "**'기타'는 그 값이 아무리 커도 절대 1위가 될 수 없다.** "
    "예1: '기타 23.2%, 없음/모름/무응답 18.4%, 보일러 6.1%, 태양광 5.0% …' → 1위는 '보일러 6.1%'. "
    "예2: '유아·어린이 11.2%, 개인 위생용품 10.8%, … 기타 23.2%, 없음/모름/무응답 18.4%' → "
    "1위는 '유아·어린이 용품 11.2%'('기타 23.2%'가 최대지만 집계라 제외). "
    "집계·비응답 항목의 비율은 답변 맨 끝에 '참고:' 로만 덧붙일 수 있다.\n"
    "5. [근거]에 비슷하지만 다른 표가 여러 개 있으면, 질문의 표현과 가장 정확히 일치하는 "
    "**표 하나만** 골라 답하고 다른 표의 수치를 섞지 마라. (예: '친환경제품 확대 희망'과 "
    "'환경표지 인증제품 확대 희망'은 별개의 표다.) 어느 표인지 출처로 분명히 밝힌다.\n"
    "6. 한국어로 간결하게."
)


# '데이터 기반 제언' 모드: 사실은 인용하고 추론은 명시적으로 분리한다("추측은 데이터가 아니다"
# 를 답변 구조로 강제 — 근거 사실은 [출처]로, 제언은 그 사실에서만 도출하고 '추론'으로 라벨).
# 깊이는 KEEP/ADD/DROP/FIX 틀로 강제하고, [방법론 주석](척도 변경 등)을 반드시 반영해
# 척도 변경 아티팩트를 '실제 추세'로 오독하지 않게 한다.
ADVISE_SYSTEM_PROMPT = (
    "너는 '친환경 소비 인지도 조사'(2023~2025 정형 데이터)에 근거해 '데이터 기반 제언'을 "
    "하는 분석가다.\n"
    "규칙(반드시 지켜라):\n"
    "1. 결론을 먼저 쓴다. 정확히 `### 💡 제언(추론)` 헤딩을 맨 위에 두고, 그 아래에 "
    "정확히 `### 📊 근거 사실` 헤딩을 둔다(헤딩 표기를 바꾸지 마라 — 화면이 이 헤딩으로 구조화한다).\n"
    "   '💡 제언(추론)' — 아래 KEEP/ADD/DROP/FIX 네 갈래. 각 제언 끝에 근거가 된 [출처]를 붙인다.\n"
    "   '📊 근거 사실' — 위 제언이 인용한 수치·추세를 [근거]에서 뽑아 하단에 정리. 각 항목 끝에 [출처].\n"
    "2. 출처 표기: [근거] 자료 머리에 적힌 '실제 파일명'을 그대로 써라. '파일' 같은 자리표시자를 "
    "그대로 쓰지 마라. 예: [출처: 2025년 친환경생활·소비 국민 인지도 조사 결과보고서.pdf p.31-32]. "
    "페이지가 없는 자료(방법론 주석 등)는 파일명만: [출처: 방법론 주석(큐레이션)].\n"
    "3. 제언은 반드시 다음 네 갈래를 각각 **정확히 이 헤딩으로** 채운다: `#### KEEP(유지)` "
    "`#### ADD(신설)` `#### DROP/축소` `#### FIX(설계 교정)` "
    "(해당 없으면 그 헤딩 아래 '데이터로 판단 불가'라고 명시):\n"
    "   · KEEP(유지) — 2개 연도 이상에서 추세가 뚜렷하거나 꾸준히 높은 문항만. "
    "1개 연도만 있으면 KEEP이 아니라 '관찰 필요(단일 연도)'로 분류하라.\n"
    "   · ADD(신설) — 데이터에 공백이 보여 새로 물어야 할 것(단, 근거로 공백을 지목).\n"
    "   · DROP/축소 — 변별력이 낮거나(수년째 포화·정체) 정보가 적은 문항.\n"
    "   · FIX(설계 교정) — 연도 비교를 왜곡하는 척도·정의 불일치의 표준화. "
    "[근거]에 '[방법론 주석]'이 있으면 반드시 반영해, 척도 변경으로 생긴 급등·급락을 "
    "'실제 변화'로 제언하지 말고 'FIX(척도 표준화)' 대상으로 지목하라.\n"
    "4. 제언은 오직 [근거]의 사실(+방법론 주석)에서만 도출한다. 데이터가 뒷받침하지 않으면 "
    "지어내지 말고 '데이터로 판단 불가'라고 명시한다.\n"
    "5. 순위·집계 처리는 사실 인용과 동일하게('기타·없음·소계·합계·전체' 등은 순위에서 제외).\n"
    "6. [근거]에 '[외부 맥락 <연도>]'(그해 뉴스·사회적 사건)이 있으면, 데이터가 그해(또는 직전 "
    "해) 어떻게 움직였는지와 연결해 '왜 그렇게 변했을 수 있는지' 상황에 맞는 해석을 덧붙여라"
    "(예: 그린워싱 적발 급증·가이드라인 발간 → 표시·광고 신뢰 하락의 개연적 맥락). 단 반드시 "
    "'상관·맥락일 뿐 인과가 아님'을 명시하고, 사건은 [출처: …]로 인용하라. 데이터 변화가 없거나 "
    "관련 사건이 없으면 억지로 엮지 말고 생략하라(설문 데이터 연도 밖 사건은 배경 설명으로만).\n"
    "7. [근거]에 '[보고서 시사점 <연도>]'(그해 결과보고서 요약·시사점 절의 연구원 결론)이 "
    "있으면, 정량 수치만 나열하지 말고 그 정성적 진단을 답변에 녹여 인용하라 — '<연도>년 보고서 "
    "시사점에 따르면~' 또는 'OO페이지에 따르면~'처럼 출처와 함께. 외부 맥락(뉴스)과 달리 이는 "
    "보고서 자체의 결론이므로 근거로 직접 인용할 수 있다(단 [근거]에 있는 시사점만 — 없으면 "
    "지어내지 마라).\n"
    "8. 한국어로 간결하되, 네 갈래를 형식적으로 채우지 말고 근거가 있는 것만 구체적으로."
)


# '2023년'처럼 뒤에 한글이 붙어도 잡되, 'p.74'·'85.2'·긴 숫자는 연도로 오인하지 않게
# 단어 경계(\b) 대신 '숫자가 앞뒤로 붙지 않은 4자리'로 매칭한다.
# 추가로 '2000명'(표본수)·'1900원'(가격)처럼 4자리 뒤에 수량/화폐 단위가 붙는 경우는
# 연도가 아니므로 negative lookahead 로 배제한다(연도 오인 → 잘못된 필터 → 오거부 방지).
_YEAR_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?!\d)"
    r"(?!\s*(?:명|원|가구|개|건|위|회|점|표|천|만|억|퍼센트|％|%|kg|g|ml|리터|L))"
)


def _detect_year(query: str) -> str | None:
    """ 질문에 연도가 '하나만' 명시되면 그 연도를 검색 필터로 쓴다.
        - 임베딩이 연도 토큰을 약하게 반영해, '2023년 …' 질문에도 다른 해 청크가
          더 앞에 와 정답이 rerank 창 밖으로 밀리는 문제를 막는다(실측 회귀).
        - 두 연도 이상(연도 비교 질문)이면 필터하지 않는다(모든 해를 검색 가능하게). """
    years = set(_YEAR_RE.findall(query))
    return years.pop() if len(years) == 1 else None


# 답변 상세도(길이·깊이) — 같은 근거로 서술 수준만 달리한다. 사실/제언 모드 공통.
DETAIL_GUIDE = {
    "요약": (
        "\n\n[상세도: 요약] 가능한 한 짧게. 핵심 결론 위주로 각 항목·제언을 1~2줄 "
        "요지로만 쓴다(제언 모드면 KEEP/ADD/DROP/FIX 각 갈래를 한 줄 요지로)."
    ),
    "표준": "",   # 기본 — 추가 지침 없음
    "상세": (
        "\n\n[상세도: 상세] 근거의 구체 수치·연도·문항명·기관명을 명시하고 항목별로 "
        "구조화해 깊이 있게 설명한다. 각 판단의 근거와 함의, 그리고 한계(데이터가 다루지 "
        "않는 부분)까지 짚는다. 단, 근거에 없는 내용은 지어내지 말고 인용을 유지한다."
    ),
}
DEFAULT_DETAIL = "표준"


def _with_detail(system: str, detail: str) -> str:
    return system + DETAIL_GUIDE.get(detail, "")


# 6.4 질문 재작성 — 짧거나 구어체인 질문을 검색에 유리하게 정규화·확장한다(recall↑).
#   뜻은 그대로 두고 정식 명칭·동의어를 덧붙인 '검색어'만 만든다. 연도 판단·라우팅·화면
#   표시는 원 질문을 쓰고 여기 결과는 search()에만 넘긴다(연도 토큰 훼손·의미 변질 방지).
REWRITE_SYSTEM_PROMPT = (
    "너는 '친환경 소비 인지도 조사' 벡터 검색을 돕는 질문 재작성기다. 사용자 질문을 "
    "검색에 유리하도록 한 줄로 정규화·확장해라.\n"
    "- 뜻은 그대로 두고 조사·구어체를 걷어낸다. 핵심 명사(제도·지표의 정식 명칭)와 "
    "동의어를 덧붙인다. 예: '그린카드 아는 사람' → '그린카드 인지도 인지 여부 알고 있음 비율'.\n"
    "- 질문에 연도가 있으면 그대로 남긴다. 없는 정보를 지어내지 마라.\n"
    "- 설명·따옴표 없이 재작성된 검색어 '한 줄'만 출력한다."
)


def rewrite_query(query: str) -> str:
    """ 검색 recall을 높이도록 질문을 정규화/확장한 '검색어'를 돌려준다.
        FAKE 모드거나 실패하면 원 질문을 그대로 돌려준다(결정적·안전). """
    if os.getenv("RAG_FAKE_LLM"):
        return query
    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=REWRITE_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or query
    except Exception:
        return query


@dataclass
class Answer:
    text: str
    hits: list[Hit]
    timings: dict = field(default_factory=dict)   # {"retrieval","generate","total"} 초
    rewritten: str = ""   # 6.4 재작성된 검색어(재작성을 쓴 경우만). 화면 투명성용.


# --- advise 답변 구조 파싱(화면 구조화 렌더용) --------------------------------
# 프롬프트가 강제한 헤딩(### 💡 제언 / #### KEEP… / ### 📊 근거 사실)대로 나눠 돌려줄 뿐,
# LLM 이 쓰지 않은 구조를 합성하지 않는다("추측은 데이터가 아니다").
# 파싱이 어긋나면 None → 화면은 원문 마크다운을 그대로 보여준다(필수 폴백).

_ADVISE_KINDS = ("KEEP", "ADD", "DROP", "FIX")


@dataclass
class AdviseSections:
    preamble: str                        # 첫 헤딩 이전 텍스트(있으면 그대로 보여준다)
    advice: list[tuple[str, str, str]]   # (종류 KEEP/ADD/DROP/FIX, 헤딩 원문, 본문)
    facts: str                           # '근거 사실' 본문("" 가능)


def parse_advise_sections(text: str) -> AdviseSections | None:
    """ advise 답변을 헤딩 단위로 나눈다. 제언 갈래가 2개 미만이면 None(원문 폴백). """
    # (종류, 헤딩 원문, 본문 줄들) — 종류: PRE(서문)/SKIP(컨테이너 헤딩)/KEEP…/FACTS
    sections: list[tuple[str, str, list[str]]] = [("PRE", "", [])]
    for ln in (text or "").splitlines():
        m = re.match(r"^\s{0,3}#{2,4}\s*(.+)$", ln)
        if m:
            head = m.group(1).strip()
            kind = next((kd for kd in _ADVISE_KINDS if kd in head.upper()), None)
            if kind is None and "근거 사실" in head:
                kind = "FACTS"
            if kind is None and "제언" in head:
                kind = "SKIP"   # '### 💡 제언(추론)' 자체는 갈래를 담는 컨테이너 헤딩
            if kind:
                sections.append((kind, head, []))
                continue
        sections[-1][2].append(ln)
    advice = [(kd, head, "\n".join(body).strip())
              for kd, head, body in sections if kd in _ADVISE_KINDS]
    if len(advice) < 2:
        return None
    facts = "\n\n".join("\n".join(body).strip()
                        for kd, _, body in sections if kd == "FACTS").strip()
    # 서문 = 첫 헤딩 이전(PRE) + 컨테이너 헤딩('### 💡 제언') 바로 아래 서술(SKIP) —
    # LLM 이 쓴 텍스트를 조용히 버리지 않는다(구조화는 분할일 뿐).
    pre_parts = [t for kd, _, body in sections if kd in ("PRE", "SKIP")
                 for t in ["\n".join(body).strip()] if t]
    preamble = "\n\n".join(pre_parts)
    return AdviseSections(preamble=preamble, advice=advice, facts=facts)


def _merge_hits(*groups: list[Hit]) -> list[Hit]:
    """ 여러 검색 결과를 chunk_id 기준 중복 제거하며 순서대로 합친다(먼저 온 것 우선). """
    seen: set[str] = set()
    out: list[Hit] = []
    for g in groups:
        for h in g:
            if h.chunk_id not in seen:
                seen.add(h.chunk_id)
                out.append(h)
    return out


def _advise_retrieve(query: str, year: str | None, routed: str | None,
                     fetch: int) -> list[Hit]:
    """ '데이터 기반 제언'용 다면 검색 — 한 번의 유사도 검색으론 놓치는 축을 각각 모아 합친다.
        (1) 추세/사실   : 질문 그대로.
        (2) 장벽/개선   : 실천을 막는 이유·불편·개선 축(제언에 필요한 맥락).
        (3) 방법론 주석 : 척도 변경 등 '비교 유의' 지식청크(유사도가 낮아도 반드시 포함).
        (4) 외부 맥락   : 그해 뉴스·사건(상황 대조).
        (5) 보고서 시사점: 요약·시사점 절의 연구원 결론(정책적 진단을 함께 인용하게). """
    base = search(query, k=fetch, year=year, std_id=routed)
    if routed and not base:
        base = search(query, k=fetch, year=year)
    if year and not base:
        base = search(query, k=fetch, std_id=routed) or search(query, k=fetch)
    barrier = search(f"{query} 장벽 불편 어려움 개선 이유", k=4)
    # 방법론 지식청크는 종류로 좁혀 뽑는다 — 일반 유사도로는 상위에 안 올라와 누락되던 것.
    method = search(query, k=6, parser_type="methodology", rerank=False)
    # 외부 맥락(그해 뉴스·사건): 데이터 변화를 상황과 대조해 해석하게 한다(상관·인과 아님).
    context = search(query, k=6, parser_type="external_context", rerank=False)
    # 보고서 시사점(요약·시사점 절의 연구원 결론): 유사도 낮아도 종류로 좁혀 포함해
    # 정량 수치에 '당시 정책적 진단'을 함께 인용하게 한다(빈 지식소스면 자연히 0건).
    implication = search(query, k=6, parser_type="implication", rerank=False)
    return _merge_hits(base, barrier, method, context, implication)


def _build_context(hits: list[Hit]) -> str:
    blocks = []
    for i, h in enumerate(hits, start=1):
        m = h.metadata
        page = (m.get("page") or "").strip()
        loc = f"{m.get('source','')} p.{page}" if page else f"{m.get('source','')}"
        blocks.append(f"[근거 {i}] (출처: {loc})\n{h.text}")
    return "\n\n".join(blocks)


def answer(query: str, k: int = 5, year: str | None = None, mode: str = "cite",
           detail: str = DEFAULT_DETAIL, rewrite: bool = False) -> Answer:
    """ 질문에 대해 검색→근거 답변을 생성한다. 단계별 소요시간도 함께 돌려준다.
        mode='cite'   : 사실만 출처 인용(기본).
        mode='advise' : '데이터 기반 제언' — 근거 사실(인용) + 제언(추론) 분리.
        detail        : '요약' | '표준' | '상세' — 서술 길이·깊이(근거는 동일).
        rewrite       : True면 질문을 검색어로 재작성해 recall을 높인다(6.4).
                        연도 판단·라우팅·화면 표시는 원 질문을 그대로 쓴다. """
    t0 = time.time()

    # 테스트/검증 모드: 실제 임베딩·LLM 없이 결정적 스텁(무료·빠름). 인용 형식 유지.
    if os.getenv("RAG_FAKE_LLM"):
        # 출처 카드 UI 까지 검증할 수 있게 가짜 근거 1건을 함께 돌려준다.
        # source 는 어디에도 없는 파일명(sample.pdf) — '원문 페이지' 폴백 경로가 결정적.
        fake_hit = Hit(
            chunk_id="fake-1",
            text="(테스트 근거) 예시 지표 인지율 85.2% — 스텁 청크.",
            metadata={"year": "2025", "std_id": "예시_지표",
                      "source": "sample.pdf", "page": "1"},
            score=0.99,
        )
        if mode == "advise":
            # 프롬프트의 헤딩 계약과 같은 형식 — 구조화 렌더 경로를 E2E 로 검증 가능하게.
            stub = (
                "### 💡 제언(추론)\n"
                "#### KEEP(유지)\n- (테스트) 예시 지표는 추세가 뚜렷해 유지. [출처: sample.pdf p.1]\n"
                "#### ADD(신설)\n- (테스트) 데이터 공백 주제를 신설. [출처: sample.pdf p.1]\n"
                "#### DROP/축소\n- (테스트) 변별력 낮은 문항 축소. [출처: sample.pdf p.1]\n"
                "#### FIX(설계 교정)\n- (테스트) 척도 표준화 필요. [출처: 방법론 주석(큐레이션)]\n"
                "### 📊 근거 사실\n- (테스트) 예시 인지율 85.2%. [출처: sample.pdf p.1]\n"
            )
        else:
            stub = "(테스트 답변) 예시 인지율은 85.2% 입니다. [출처: sample p.1]"
        return Answer(text=stub, hits=[fake_hit],
                      timings={"retrieval": 0.0, "generate": 0.0, "total": 0.0})

    # 호출자가 연도를 지정하지 않았으면 질문에서 자동 감지(단일 연도일 때만).
    if year is None:
        year = _detect_year(query)

    # 질문이 명확히 한 표를 가리키면 그 표로 좁혀 검색한다(비슷한 표 간 LLM 변동 제거).
    routed = route(query)
    # 6.4 검색어 재작성(선택) — 검색만 재작성어로, 연도·라우팅·표시는 원 질문 유지.
    search_query = rewrite_query(query) if rewrite else query
    rewritten = search_query if (rewrite and search_query != query) else ""
    # 제언 모드는 추세를 종합해야 하니 맥락을 넉넉히 모은다.
    fetch = max(k, 10) if mode == "advise" else k

    t_ret = time.time()
    if mode == "advise":
        # 제언 모드는 추세·장벽·방법론을 각각 뽑아 합친다(다면 검색).
        hits = _advise_retrieve(search_query, year, routed, fetch)
    else:
        hits = search(search_query, k=fetch, year=year, std_id=routed)
        # 라우팅이 너무 좁혀 결과가 없으면(예: 그 연도엔 그 표가 없음) 표 필터 없이 재검색.
        if routed and not hits:
            hits = search(search_query, k=fetch, year=year)
        # 연도 필터가 결과를 0으로 만들면(예: 데이터 없는 미래연도 '2026') 필터를 풀어 전체에서 검색.
        # → "2026 설문 어떻게?" 같은 질문도 3개년 근거로 답할 수 있게 한다.
        if year and not hits:
            hits = search(search_query, k=fetch, std_id=routed) or search(search_query, k=fetch)
    retrieval = round(time.time() - t_ret, 2)

    if not hits:
        return Answer(text="문서에서 찾을 수 없습니다. (검색 결과 없음)", hits=[],
                      timings={"retrieval": retrieval, "generate": 0.0,
                               "total": round(time.time() - t0, 2)}, rewritten=rewritten)

    client = get_client()
    if mode == "advise":
        system = ADVISE_SYSTEM_PROMPT
        instruct = ("`### 💡 제언(추론)` 헤딩 아래 `#### KEEP(유지)`/`#### ADD(신설)`/"
                    "`#### DROP/축소`/`#### FIX(설계 교정)` 헤딩으로 제언을 먼저 쓰고, "
                    "그 아래 `### 📊 근거 사실` 헤딩으로 근거를 정리해라(헤딩 표기 변경 금지). "
                    "출처는 [근거] 머리의 실제 파일명을 그대로 인용하고 '파일' 같은 "
                    "자리표시자를 쓰지 마라. 근거 없는 제언은 하지 마라.")
    else:
        system = SYSTEM_PROMPT
        instruct = ("위 [근거]에 있는 내용만으로 답하고, 수치마다 [근거] 머리의 실제 파일명으로 "
                    "[출처: 파일명 p.쪽]을 붙여라('파일' 자리표시자 금지).")
    system = _with_detail(system, detail)
    user_prompt = f"[근거]\n{_build_context(hits)}\n\n[질문]\n{query}\n\n{instruct}"
    t_gen = time.time()
    resp = client.chat.completions.create(
        model=ANSWER_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
    )
    generate = round(time.time() - t_gen, 2)
    return Answer(
        text=resp.choices[0].message.content, hits=hits,
        timings={"retrieval": retrieval, "generate": generate,
                 "total": round(time.time() - t0, 2)},
        rewritten=rewritten,
    )


# 6.7 예시 질문 — 실제 인덱싱된 문항으로 '답할 수 있는' 추천 질문을 만든다.
#   데이터에 실제 있는 문항명만 씨앗으로 줘 '답 못 하는 질문'을 만들지 않는다
#   ("추측은 데이터가 아니다"를 추천에도 적용). FAKE·실패 시 정적 폴백으로 결정적.
EXAMPLE_Q_SYSTEM_PROMPT = (
    "너는 '친환경 소비 인지도 조사' 데이터로 답할 수 있는 추천 질문을 만드는 도우미다.\n"
    "- 아래에 준 '문항(주제)'과 '연도'로 실제 답할 수 있는 질문만 만든다. 데이터에 없을 "
    "법한 주제(가격·브랜드·판매량 등)는 절대 만들지 마라.\n"
    "- 자연스러운 한국어 질문을 한 줄에 하나씩. 번호·기호·따옴표 없이 질문만 출력.\n"
    "- 특정 연도 수치 질문과 '3개년 추세' 질문을 섞어 다양하게 낸다."
)
_FALLBACK_QUESTIONS = [
    "2025년 그린카드 인지도는 얼마인가요?",
    "친환경제품을 구매할 때 가장 큰 장벽은 무엇인가요?",
    "환경표지 인증제품 인지도는 3개년간 어떻게 변했나요?",
    "3개년 추세로 보아 2026 설문은 어떻게 설계하면 좋을까?",
]


def _question_seeds() -> tuple[list[str], list[str]]:
    """ 인덱싱된 데이터에서 (문항 라벨 표본, 연도 목록)을 뽑는다. """
    from rag.retrieval import chunking   # 지연 import(순환 방지)
    rows = chunking.load_rows()
    labels: list[str] = []
    seen: set[str] = set()
    years: set[str] = set()
    for r in rows:
        y = r.get("year")
        if y:
            years.add(str(y))
        lab = (r.get("std_label") or "").strip()
        if lab and lab not in seen:
            seen.add(lab)
            labels.append(lab)
    return labels, sorted(years)


def suggest_questions(n: int = 4) -> list[str]:
    """ 인덱싱된 데이터에서 '답할 수 있는' 추천 질문 n개를 생성한다.
        FAKE 모드·실패·씨앗 없음이면 정적 폴백을 돌려준다(결정적). """
    if os.getenv("RAG_FAKE_LLM"):
        return _FALLBACK_QUESTIONS[:n]
    try:
        seeds, years = _question_seeds()
        if not seeds:
            return _FALLBACK_QUESTIONS[:n]
        client = get_client()
        topic = ", ".join(seeds[:20])
        resp = client.chat.completions.create(
            model=EXAMPLE_Q_MODEL,
            temperature=0.5,
            messages=[
                {"role": "system", "content": EXAMPLE_Q_SYSTEM_PROMPT},
                {"role": "user",
                 "content": f"연도: {', '.join(years)}\n문항(주제): {topic}\n\n위에서 {n}개 만들어라."},
            ],
        )
        text = resp.choices[0].message.content or ""
        # 앞머리 목록 기호만 제거(1./2)/-/•). '2023년…'의 연도를 먹지 않게 1~2자리로 한정.
        qs = [re.sub(r"^\s*(?:[-•·*]|\d{1,2}[.)])\s+", "", ln).strip() for ln in text.splitlines()]
        qs = [q for q in qs if len(q) > 6]
        return qs[:n] or _FALLBACK_QUESTIONS[:n]
    except Exception:
        return _FALLBACK_QUESTIONS[:n]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = [a for a in sys.argv[1:] if a != "--advise"]
    mode = "advise" if "--advise" in sys.argv[1:] else "cite"
    query = " ".join(args) or "2023년에 확대되길 바라는 친환경제품 1위는?"
    res = answer(query, mode=mode)
    print(f"질문: {query}\n" + "=" * 60)
    print(res.text)
    print("-" * 60 + "\n근거 출처:")
    for i, h in enumerate(res.hits, start=1):
        print(f"  [{i}] {h.metadata.get('year')} {h.metadata.get('std_id')} — {h.locator} (score {h.score})")


if __name__ == "__main__":
    main()
