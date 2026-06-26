# rag/retriever.py
# -----------------------------------------------------------------------------
# 6.3 검색 (Retrieval)
#
# 이 파일의 역할:
#   - 사용자 질문을 임베딩해 Chroma 인덱스에서 가장 가까운 청크 top-k 를 찾는다.
#   - 각 결과에 출처 메타(source/page/std_id)와 유사도를 붙여 돌려준다.
#     → 답변(answer.py)이 이 결과만 근거로 쓰고 출처를 인용한다.
#
# 실행(단독 검색 테스트):
#   uv run python rag/retriever.py "2023년에 확대되길 바라는 친환경제품 1위는?"
# -----------------------------------------------------------------------------

from __future__ import annotations

import sys
from dataclasses import dataclass

try:
    from rag.extract import get_client
    from rag.config import EMBEDDING_MODEL
    from rag.index import get_collection, embed_texts
except ImportError:
    from extract import get_client
    from config import EMBEDDING_MODEL
    from index import get_collection, embed_texts


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


def search(query: str, k: int = 5, year: str | None = None) -> list[Hit]:
    """ 질문과 가까운 청크 top-k. year 를 주면 해당 연도로 필터. """
    client = get_client()
    qvec = embed_texts(client, [query])[0]

    where = {"year": str(year)} if year else None
    col = get_collection()
    res = col.query(
        query_embeddings=[qvec],
        n_results=k,
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
    return hits


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
