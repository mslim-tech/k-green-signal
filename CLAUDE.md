# RAG Lab — 프로젝트 규칙

## 보안
- API Key는 `.env`에서만 읽는다. 코드에 직접 쓰지 않는다.
- `.env`, `chr

## 프로젝트 구조

- Streamlit 앱 진입점은 `app.py`로 둔다.
- 처음에는 `app.py` 하나로 시작하되, RAG 기능이 추가되는 시점부터 기능별 파일로 분리한다.
- 문서 진단 기능은 `rag/ingestion.py`로 분리한다.
- Chunking 기능은 `rag/chunking.py`로 분리한다.
- Vector DB 구축 기능은 `rag/index.py`로 분리한다.
- 검색 기능은 `rag/retriever.py`로 분리한다.
- 결과 파일이 필요해지는 시점에 `outputs/` 폴더를 만든다.
- 평가 질문이 필요해지는 시점에 `eval/` 폴더를 만든다.
- 샘플 문서가 필요하면 `data/` 폴더를 사용한다.


## Metadata 규칙
각 Chunk metadata에는 최소한 다음 항목을 유지한다.

- source
- page
- parser_type
- chunk_id
- token_count
- warning

## 작업 방식
- 큰 변경 전에는 먼저 계획과 파일 구조를 제안한다.
- 사용자가 승인한 뒤 구현한다.
- 코드는 비전공자가 읽기 쉽게 작성한다.
- 각 파일 상단에는 이 파일의 역할을 주석으로 적는다.
- 한 번에 RAG 전체를 구현하지 말고, 단계별로 구현한다.