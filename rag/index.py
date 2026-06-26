# rag/index.py
# -----------------------------------------------------------------------------
# 6.2 임베딩 · 인덱싱 (Indexing)
#
# 이 파일의 역할:
#   - 6.1 청크(chunks.jsonl)를 OpenAI 임베딩(text-embedding-3-small)으로 벡터화하고,
#     Chroma 벡터 DB(outputs/chroma/)에 출처 메타와 함께 저장한다.
#   - 검색(retriever.py)이 이 인덱스를 읽어 질문과 가까운 청크를 찾는다.
#
# 보안: API Key 는 .env 의 OPENAI_API_KEY 에서만 읽는다(extract.get_client 재사용).
#
# 실행:
#   uv run python rag/index.py          # chunks.jsonl → Chroma 인덱스 구축
# -----------------------------------------------------------------------------

from __future__ import annotations

import sys
from pathlib import Path

try:
    from rag.extract import get_client
    from rag.config import EMBEDDING_MODEL
    from rag import chunking
except ImportError:
    from extract import get_client
    from config import EMBEDDING_MODEL
    import chunking


OUTPUT_DIR = Path("outputs")
CHROMA_DIR = OUTPUT_DIR / "chroma"
COLLECTION = "kgs_facts"   # k-green-signal 정형 사실 청크
EMBED_BATCH = 100          # 임베딩 호출당 청크 수


def embed_texts(client, texts: list[str], model: str = EMBEDDING_MODEL) -> list[list[float]]:
    """ 텍스트 목록을 배치로 임베딩한다. (입력 순서대로 벡터 반환) """
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i:i + EMBED_BATCH]
        resp = client.embeddings.create(model=model, input=batch)
        # resp.data 는 입력 순서를 보장한다.
        vectors.extend(d.embedding for d in resp.data)
    return vectors


def get_collection(reset: bool = False):
    """ Chroma 영구 컬렉션을 연다. reset=True 면 기존 컬렉션을 지우고 새로. """
    import chromadb
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if reset:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
    # 임베딩은 우리가 직접 만들어 넣으므로 embedding_function 은 지정하지 않는다.
    return client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def build_index(reset: bool = True) -> int:
    """ 청크를 임베딩해 Chroma 에 적재한다. 적재한 청크 수를 반환. """
    chunks = chunking.load_chunks()
    if not chunks:
        raise RuntimeError("청크가 없습니다. 먼저 rag/chunking.py 를 실행하세요.")

    client = get_client()
    texts = [c["text"] for c in chunks]
    print(f"임베딩 중... {len(texts)} 청크 ({EMBEDDING_MODEL})")
    vectors = embed_texts(client, texts)

    col = get_collection(reset=reset)
    col.upsert(
        ids=[c["id"] for c in chunks],
        embeddings=vectors,
        documents=texts,
        metadatas=[c["metadata"] for c in chunks],
    )
    return len(chunks)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    n = build_index(reset=True)
    col = get_collection()
    print("\n" + "=" * 60)
    print(f"인덱싱 완료 — {n} 청크 → Chroma '{COLLECTION}' (총 {col.count()}개)")
    print(f"💾 {CHROMA_DIR}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
