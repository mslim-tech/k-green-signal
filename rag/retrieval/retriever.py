# rag/retrieval/retriever.py
# -----------------------------------------------------------------------------
# 6.3 검색 (Retrieval)
#
# 이 파일의 역할:
#   - 사용자 질문을 임베딩해 Chroma 인덱스에서 가장 가까운 청크 top-k 를 찾는다.
#   - 각 결과에 출처 메타(source/page/std_id)와 유사도를 붙여 돌려준다.
#     → 답변(answer.py)이 이 결과만 근거로 쓰고 출처를 인용한다.
#
# 실행(단독 검색 테스트):
#   uv run python -m rag.retrieval.retriever "2023년에 확대되길 바라는 친환경제품 1위는?"
# -----------------------------------------------------------------------------

from __future__ import annotations

import sys
import json
import os
from dataclasses import dataclass

from rag.ingest.extract import get_client
from rag.core.config import RERANKER_MODEL
from rag.retrieval.index import get_collection, embed_texts
@dataclass
class Hit:
    """ 검색 결과 한 건. """
    chunk_id: str
    text: str
    metadata: dict
    score: float   # 1 - cosine_distance (클수록 가까움, 0~1)

    @property
    def locator(self) -> str:
        m = self.metadata
        return f"{m.get('source','')} p.{m.get('page','')}".strip()


# 리랭커가 돌려줄 형식: 관련도 높은 순으로 후보 번호(1-based) 나열
_RERANK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ranked"],
    "properties": {"ranked": {"type": "array", "items": {"type": "integer"}}},
}
_RERANK_SYSTEM = (
    "너는 검색 결과 '재정렬기'다. 사용자의 질문에 **실제로 답하는 데 도움이 되는** "
    "자료일수록 앞에 오도록 후보 번호를 관련도 높은 순으로 정렬해라. "
    "관련 없는 후보는 뒤로 보내거나 빼도 된다. 번호만 반환한다."
)


def _rerank(query: str, hits: list[Hit], top_k: int) -> list[Hit]:
    """ LLM(listwise)으로 후보를 질문 관련도 순으로 재정렬해 상위 top_k 를 돌려준다.
        실패하면 원래 벡터 순서를 그대로 사용한다(안전). """
    if len(hits) <= 1:
        return hits[:top_k]
    cands = "\n".join(
        f"{i}. {h.text.replace(chr(10), ' ')[:300]}" for i, h in enumerate(hits, start=1)
    )
    user = (
        f"[질문]\n{query}\n\n[후보 자료]\n{cands}\n\n"
        f"질문에 답하는 데 관련 있는 자료 번호를 관련도 높은 순으로 정렬해 'ranked' 에 담아라."
    )
    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=RERANKER_MODEL,
            temperature=0,
            messages=[{"role": "system", "content": _RERANK_SYSTEM},
                      {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "rerank", "strict": True,
                                             "schema": _RERANK_SCHEMA}},
        )
        order = json.loads(resp.choices[0].message.content).get("ranked", [])
    except Exception:
        return hits[:top_k]

    seen: set[int] = set()
    out: list[Hit] = []
    for n in order:
        if isinstance(n, int) and 1 <= n <= len(hits) and n not in seen:
            seen.add(n)
            out.append(hits[n - 1])
    # 리랭커가 빠뜨린 후보는 원래 순서대로 뒤에 채운다(누락 방지).
    for i, h in enumerate(hits, start=1):
        if i not in seen:
            out.append(h)
    return out[:top_k]


def search(query: str, k: int = 5, year: str | None = None,
           fetch_k: int | None = None, rerank: bool = True,
           std_id: str | None = None, parser_type: str | None = None) -> list[Hit]:
    """ 질문과 가까운 청크 top-k.
        rerank=True 면 벡터로 fetch_k 개를 넓게 뽑은 뒤 LLM 으로 재정렬해 상위 k 개를 돌려준다.
        (RAG_FAKE_LLM 이면 재정렬 생략 — 테스트 결정성). year 로 연도 필터 가능.
        std_id 를 주면 그 표로만 좁혀 검색한다(질문→표 라우팅이 명확한 표를 지정할 때).
        parser_type 을 주면 그 청크 종류로만 좁힌다(예: 'methodology' 방법론 지식청크만).
    """
    use_rerank = rerank and not os.getenv("RAG_FAKE_LLM")
    fetch_k = fetch_k or (max(k * 4, 12) if use_rerank else k)

    client = get_client()
    qvec = embed_texts(client, [query])[0]

    # 연도·표·청크종류 필터를 Chroma where 로 묶는다(여럿이면 $and).
    conds = []
    if year:
        conds.append({"year": str(year)})
    if std_id:
        conds.append({"std_id": std_id})
    if parser_type:
        conds.append({"parser_type": parser_type})
    where = conds[0] if len(conds) == 1 else ({"$and": conds} if conds else None)
    col = get_collection()
    res = col.query(
        query_embeddings=[qvec],
        n_results=fetch_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    hits: list[Hit] = []
    ids = res.get("ids", [[]])[0]
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        hits.append(Hit(chunk_id=cid, text=doc, metadata=meta or {},
                        score=round(1.0 - float(dist), 4)))

    if use_rerank:
        return _rerank(query, hits, k)
    return hits[:k]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    query = " ".join(sys.argv[1:]) or "2023년에 확대되길 바라는 친환경제품 1위는?"
    print(f"질문: {query}\n" + "=" * 60)
    for i, h in enumerate(search(query, k=5), start=1):
        print(f"[{i}] score={h.score} | {h.metadata.get('year')} {h.metadata.get('std_id')} | {h.locator}")
        print("    " + h.text.replace("\n", " ")[:160] + " ...")


if __name__ == "__main__":
    main()
