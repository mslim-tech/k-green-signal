# RAG Lab — 친환경 인지도 조사 정형화 파이프라인

> 「친환경 생활·소비 국민 인지도 조사」 결과보고서(PDF)를
> **연도 통합 정형 데이터셋(Long-format)** 으로 변환하는 단계별 파이프라인.

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white">
  <img alt="uv" src="https://img.shields.io/badge/uv-managed-DE5FE9?logo=uv&logoColor=white">
  <img alt="OpenAI" src="https://img.shields.io/badge/OpenAI-gpt--5.4--mini-412991?logo=openai&logoColor=white">
  <img alt="Streamlit" src="https://img.shields.io/badge/Streamlit-app-FF4B4B?logo=streamlit&logoColor=white">
</p>

---

## 개요 (Overview)

매년 발간되는 인지도 조사 보고서는 **해마다 문항 표현·응답 척도·표기 형식이 조금씩 달라**
서로 다른 연도를 한 번에 비교하기 어렵습니다. RAG Lab은 이 비정형 PDF들을 읽어
**문항을 표준화하고 전체(국민 전체) 기준 핵심 수치를 추출해**, 연도 간 비교가 가능한
하나의 tidy 데이터셋으로 통합합니다.

- **대상**: 총 14개년 (2007, 2013~2025)
- **현재 진행**: 최근 3개년(2023~2025) PDF 5개로 파이프라인 0~3단계 구축·검증 완료
- **추출 범위**: 우선 **'전체' 핵심 수치**만 (성별·연령 등 하위집단 교차표는 추후)
- **산출물**: 표준 문항 사전 + 연도 통합 Long-format CSV

---

## 핵심 아키텍처 (Pipeline)

데이터는 다음 단계를 거쳐 흐릅니다. 각 단계는 독립 실행·검수가 가능합니다.

| 단계 | 모듈 | 하는 일 | 상태 |
|---|---|---|---|
| **0. 진단** | `rag/ingestion.py` | PDF가 디지털 텍스트인지 스캔인지, 표/이미지 구조 진단 | ✅ |
| **1. 블록 분리** | `rag/parsing.py` | 본문을 `문항 단위`로 분리 (출처·페이지 부착) | ✅ |
| **2. LLM 추출** | `rag/extract.py` | 블록 원문 → 구조화 레코드 (Structured Outputs) | ✅ |
| **3. 표준화** | `rag/standardize.py` | 연도별 문항을 표준 문항 ID로 통합 → Long CSV | ✅ |
| **4. 정제·통합** | `rag/refine.py` *(예정)* | 응답 라벨 표준화·중복 제거·의심값 검수 | ⏳ |
| **5. 검수 UI** | `app.py` *(예정)* | 저신뢰 행을 사람이 원문과 대조·수정 | ⏳ |
| **6. RAG 검색** | `rag/chunking·index·retriever.py` *(예정)* | 정형 데이터 위 자연어 질의응답 | ⏳ |

> 전체 로드맵(4~8단계 세부 체크리스트)은 [`plan.md`](./plan.md) 참고.

### 왜 LLM 추출인가
이 보고서들의 통계는 표(table)가 아니라 **서술형 문장 속 인라인 수치**와 **차트 이미지**에
들어 있고, 메타 표기도 연도마다 다릅니다 (`(N=1,000, 단위:%)` / `[BASE : 전체 (n=1,000) …]` /
`<표>` + 숫자 나열). 정규식으로 모든 형식을 쫓는 대신, **LLM이 형식 차이를 흡수**하고
스스로 `extraction_confidence`/`warning`을 매겨 **사람 검수 우선순위**를 표시합니다.

---

## 입력 형식 & 처리

- **PDF (조사 결과보고서)**: PyMuPDF로 페이지별 텍스트 추출 → 문항 블록 분리
- 현재 대상 5개 PDF는 모두 **디지털 텍스트**(선택·복사 가능)로 OCR 불필요
- 통계 수치는 서술 문장에서 추출, 도표(`<그림>`/`<표>`)는 참조로 기록 (Vision 판독은 7단계 예정)

---

## 모듈 구조

```
rag-lab/
├── app.py                # Streamlit 진입점 (현재: Baseline 문서 Q&A)
├── plan.md               # 단계별 진행 계획 (4~8단계)
├── rag/
│   ├── config.py         # 모델 중앙 설정 (한 곳에서 교체)
│   ├── ingestion.py      # 0 문서 진단
│   ├── parsing.py        # 1 문항 블록 분리
│   ├── extract.py        # 2 LLM 구조화 추출
│   └── standardize.py    # 3 문항 표준화 + 통합 CSV
├── data/                 # 입력 PDF (조사 보고서)
└── outputs/              # 산출물 (jsonl / 사전 / CSV)
```

각 문항 레코드가 보존하는 메타데이터:
`source` · `page` · `section` · `subsection` · `question_summary` ·
`response_items[{label, value}]` · `base_n` · `unit` · `multi_response` ·
`figures` · `extraction_confidence` · `warning`

---

## 산출물 (Outputs)

3개년 5개 PDF 처리 결과:

- `outputs/*.extracted.jsonl` — 연도별 추출 레코드 **285건**
- `outputs/question_dictionary.json` — 표준 문항 **114개** (이 중 **56개**가 2개년 이상 연결)
- `outputs/standardized_long.csv` — 연도 통합 tidy 데이터셋 **1,056행** (엑셀 호환 UTF-8 BOM)

예시 — 한 표준 문항의 연도 비교가 한 줄로:

```
녹색제품_인지도 : 2023 인지 51.7%  →  2024 알고있음 82.2%  →  2025 알고있음 85.2%
```

---

## 빠른 시작 (Quick Start)

```bash
# 1) 의존성 설치 (uv)
uv sync

# 2) .env 에 API Key 설정
#    OPENAI_API_KEY=sk-...

# 3) 단계별 실행
uv run python rag/ingestion.py                       # 0 진단
uv run python rag/parsing.py "data/<파일>.pdf" 5       # 1 블록 분리 확인
uv run python rag/extract.py "data/<파일>.pdf" 999 --save   # 2 전체 추출 → outputs/
uv run python rag/standardize.py                     # 3 표준화 → 통합 CSV

# (선택) 현재 Baseline Q&A 앱
uv run streamlit run app.py
```

> 보안: API Key는 **`.env`에서만** 읽으며 코드에 직접 쓰지 않습니다.

---

## 주요 설정 (Key Configuration)

모델은 [`rag/config.py`](./rag/config.py) 한 곳에서 관리합니다 (교체 시 이 파일만 수정).

| 용도 | 모델 |
|---|---|
| 추출 · 표준화 · 답변 · 재작성 · Reranker · 예시질문 · Vision | `gpt-5.4-mini` |
| 인덱싱 · 검색 임베딩 | `text-embedding-3-small` |

- **구조화 출력**: OpenAI Structured Outputs (`json_schema`, `strict`)로 환각 억제
- **벡터 DB**(6단계 예정): Chroma
- **출력 인코딩**: 한글 Windows(cp949)에서도 깨지지 않도록 UTF-8 강제

### 비용

- 임베딩: text-embedding-3-small ≈ **$0.02 / 1M 토큰**
- LLM 호출(gpt-5.4-mini): 사용량 기반 — 최신 단가는 OpenAI 요금표 참조
- 상태 표시줄/로컬 처리는 API 비용이 없습니다

---

## 기술 스택

Python 3.12 · uv · OpenAI (Chat + Embeddings) · PyMuPDF · pypdf · python-docx ·
Streamlit · ChromaDB · tiktoken · python-dotenv

---

## 진행 상황

- ✅ 0~3단계 (3개년 데이터셋 생성) + 모델 중앙화
- ⏳ 4단계 정제(라벨 표준화·중복 제거) 부터 진행 예정 — [`plan.md`](./plan.md)
