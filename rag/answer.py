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
    "4. 한국어로 간결하게."
)


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
