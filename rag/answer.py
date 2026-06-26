# rag/answer.py
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
#   uv run python rag/answer.py "2024년 환경표지 정의 인지율은?"
# -----------------------------------------------------------------------------

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field

try:
    from rag.extract import get_client
    from rag.config import ANSWER_MODEL
    from rag.retriever import search, Hit
except ImportError:
    from extract import get_client
    from config import ANSWER_MODEL
    from retriever import search, Hit


SYSTEM_PROMPT = (
    "너는 '친환경 소비 인지도 조사' 정형 데이터에 근거해 답하는 도우미다.\n"
    "규칙(반드시 지켜라):\n"
    "1. 아래 [근거]로 준 자료 안에 있는 내용만으로 답한다. 자료에 없으면 "
    "'문서에서 찾을 수 없습니다'라고만 답한다. 절대 추측하거나 지어내지 마라.\n"
    "2. 수치·사실을 말할 때마다 바로 뒤에 출처를 [출처: 파일 p.쪽] 형식으로 붙인다.\n"
    "3. 연도가 여러 개면 연도를 구분해서 답한다.\n"
    "4. 순위(1위·가장 많은·최다 등)를 물으면 '기타', '없음', '모름', '무응답', "
    "'없음/모름/무응답', '소계', '합계', '전체' 같은 집계·비응답 항목은 **순위에서 절대 "
    "1위·2위 등으로 제시하지 마라.** 이 항목이 가장 큰 값이어도 건너뛰고, 그 다음의 "
    "'실제 응답 항목(품목/보기)'을 1위로 답한다. "
    "(예: 보기 비율이 '기타 23.2%, 없음/모름/무응답 18.4%, 친환경적인 보일러 6.1%, "
    "태양광 5.0% …' 이면, '기타'·'없음/모름/무응답'은 집계·비응답이라 제외하고 1위는 "
    "'친환경적인 보일러 6.1%'다.) "
    "집계·비응답 항목의 비율은 답변 맨 끝에 '참고:' 로만 덧붙일 수 있다.\n"
    "5. [근거]에 비슷하지만 다른 표가 여러 개 있으면, 질문의 표현과 가장 정확히 일치하는 "
    "**표 하나만** 골라 답하고 다른 표의 수치를 섞지 마라. (예: '친환경제품 확대 희망'과 "
    "'환경표지 인증제품 확대 희망'은 별개의 표다.) 어느 표인지 출처로 분명히 밝힌다.\n"
    "6. 한국어로 간결하게."
)


# '2023년'처럼 뒤에 한글이 붙어도 잡되, 'p.74'·'85.2'·긴 숫자는 연도로 오인하지 않게
# 단어 경계(\b) 대신 '숫자가 앞뒤로 붙지 않은 4자리'로 매칭한다.
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def _detect_year(query: str) -> str | None:
    """ 질문에 연도가 '하나만' 명시되면 그 연도를 검색 필터로 쓴다.
        - 임베딩이 연도 토큰을 약하게 반영해, '2023년 …' 질문에도 다른 해 청크가
          더 앞에 와 정답이 rerank 창 밖으로 밀리는 문제를 막는다(실측 회귀).
        - 두 연도 이상(연도 비교 질문)이면 필터하지 않는다(모든 해를 검색 가능하게). """
    years = set(_YEAR_RE.findall(query))
    return years.pop() if len(years) == 1 else None


@dataclass
class Answer:
    text: str
    hits: list[Hit]
    timings: dict = field(default_factory=dict)   # {"retrieval","generate","total"} 초


def _build_context(hits: list[Hit]) -> str:
    blocks = []
    for i, h in enumerate(hits, start=1):
        m = h.metadata
        blocks.append(
            f"[근거 {i}] (출처: {m.get('source','')} p.{m.get('page','')})\n{h.text}"
        )
    return "\n\n".join(blocks)


def answer(query: str, k: int = 5, year: str | None = None) -> Answer:
    """ 질문에 대해 검색→근거 인용 답변을 생성한다. 단계별 소요시간도 함께 돌려준다. """
    t0 = time.time()

    # 테스트/검증 모드: 실제 임베딩·LLM 없이 결정적 스텁(무료·빠름). 인용 형식 유지.
    if os.getenv("RAG_FAKE_LLM"):
        return Answer(
            text="(테스트 답변) 예시 인지율은 85.2% 입니다. [출처: sample p.1]",
            hits=[], timings={"retrieval": 0.0, "generate": 0.0, "total": 0.0},
        )

    # 호출자가 연도를 지정하지 않았으면 질문에서 자동 감지(단일 연도일 때만).
    if year is None:
        year = _detect_year(query)

    t_ret = time.time()
    hits = search(query, k=k, year=year)
    retrieval = round(time.time() - t_ret, 2)

    if not hits:
        return Answer(text="문서에서 찾을 수 없습니다. (검색 결과 없음)", hits=[],
                      timings={"retrieval": retrieval, "generate": 0.0,
                               "total": round(time.time() - t0, 2)})

    client = get_client()
    user_prompt = (
        f"[근거]\n{_build_context(hits)}\n\n"
        f"[질문]\n{query}\n\n"
        "위 [근거]에 있는 내용만으로 답하고, 수치마다 [출처: 파일 p.쪽]을 붙여라."
    )
    t_gen = time.time()
    resp = client.chat.completions.create(
        model=ANSWER_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    generate = round(time.time() - t_gen, 2)
    return Answer(
        text=resp.choices[0].message.content, hits=hits,
        timings={"retrieval": retrieval, "generate": generate,
                 "total": round(time.time() - t0, 2)},
    )


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    query = " ".join(sys.argv[1:]) or "2023년에 확대되길 바라는 친환경제품 1위는?"
    res = answer(query)
    print(f"질문: {query}\n" + "=" * 60)
    print(res.text)
    print("-" * 60 + "\n근거 출처:")
    for i, h in enumerate(res.hits, start=1):
        print(f"  [{i}] {h.metadata.get('year')} {h.metadata.get('std_id')} — {h.locator} (score {h.score})")


if __name__ == "__main__":
    main()
