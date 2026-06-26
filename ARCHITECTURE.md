# RAG Lab — 목표 아키텍처 설계서 (ARCHITECTURE.md)

> 작성: 2026-06-26 · 근거: NotebookLM / ChatGPT(file_search) / Gemini / Claude API 조사
> 핵심 원칙(사용자 확정): **"추측은 데이터가 아니다."**
> 문서에 실제로 있는 것만, 출처와 함께 DB에 넣는다. 시스템의 답/판별은 그 출처(grounding)에 근거한다.
> LLM·휴리스틱의 불확실한 판단은 **데이터가 아니라 '검토 대기'** 로만 남긴다.

---

## 0. 왜 바꾸는가 (조사 결론)

상용 시스템(NotebookLM·ChatGPT·Gemini·Claude)의 공통점:
1. **PDF를 비전/네이티브로 읽는다** — 텍스트만 뽑아 표를 깨뜨리지 않는다. (우리 빵구의 근본 원인)
2. **답변을 근거(citation)에 묶는다** — 원문 구절·페이지에 grounding, 환각 차단.

우리의 격차: ①파싱이 텍스트라 표 붕괴 ②출처가 페이지 수준까지만(구절 없음) ③답변이 "문서 전체 통째로 프롬프트"라 grounding 없음 ④정제에 휴리스틱 '판단'이 데이터에 섞임.

→ **파싱·DB·답변을 한 축으로 다시 설계한다.**

---

## 1. 레이어 구조 (데이터 흐름)

```
[PDF 원본]  data/*.pdf                         ← 진실의 원천(불변)
   │
   ▼  (1) 파싱: 충실 추출 + 출처 부착
[추출물]  outputs/*.extracted.jsonl            ← 값 + provenance(page/표번호/원문구절)
   │
   ▼  (2) 정형화·정제 (표준화→라벨→중복→플래그)
[정형 DB]  standardized_long.* (CSV/Parquet)    ← '사실'만. 불확실은 검토큐로 분리
   │
   ├──────────────▼  (3) 청킹 + 임베딩
   │           [Vector DB]  Chroma              ← 검색용. 청크마다 출처 메타 보존
   │                │
   │                ▼  (4) 검색 + 근거 답변
   │           [RAG 답변]  app.py Q&A 탭         ← page/구절 인용. 근거 없으면 "없음"
   │
   ▼  (5) 검수 (사람)
[보정]  corrections.jsonl                       ← 사람이 grounding 보고 확정한 것만
```

**불변 규칙(원칙 구현):**
- (2) 정형 DB에는 **충실 추출값만** 들어간다. 비전/휴리스틱의 불일치·추측은 (5) 검토큐로.
- (4) 답변은 (3)에서 **검색된 청크만** 근거로 쓰고, 모든 주장에 **출처(source+page)** 를 붙인다. 검색 결과에 없으면 답을 지어내지 않는다.
- 제(어시스턴트)의 휴리스틱 판단(퍼지매칭·자동 덮어쓰기)은 데이터에 직접 쓰지 않는다 → `discrepancy`로 검수.

---

## 2. 파싱 레이어 (1) — 충실 추출 + 출처

| 모듈 | 역할 | 상태 |
|---|---|---|
| `parsing.py` | 본문을 문항 블록으로 분리(서술형 수치) | 있음 |
| `extract.py` | 블록 텍스트 → LLM 구조화 추출 | 있음 |
| `extract_vision.py` | **표 블록: 페이지 이미지 → 멀티모달 추출** | 있음(신규) |
| `refill_vision.py` | 빵구 블록 비전 재추출 → **검토 후보 생성**(자동 데이터 반영 최소화) | 있음(정책 재검토) |

**출처(provenance) 필수 항목** — 추출 레코드마다:
`source`(파일), `page_start/end`, `section/subsection`, `figure/table id`(예 `<표 3-60>`), 그리고 가능하면 **`source_quote`(원문 구절)**. 이게 grounding의 씨앗.

**라우팅 규칙**: 블록이 표 중심(빈칸·합계이상·`<표>`)이면 비전, 서술형이면 텍스트. (Claude/Gemini가 PDF를 비전+텍스트로 같이 보는 방식의 경량판)

---

## 3. 정형 DB 레이어 (2) — 사실만

- 기존 `standardized_long.*` 체인 유지 (3→4.1→4.2→4.3→4.4). 이게 '정형 사실' DB.
- **원칙 적용**: refine/dedup/refill의 **자동 판단을 보수화**. 값 충돌·라벨 모호·비전 불일치는 **`flag`/`discrepancy`로 표시만** 하고 `review_queue.csv`로. 사람이 `corrections.jsonl`로 확정해야 정식 값이 됨.
- 컬럼에 provenance 유지(source/page/figure). 추후 Parquet 병행 가능.

---

## 4. Vector DB 레이어 (3) — 청킹 + 임베딩 (신규)

| 모듈 | 역할 |
|---|---|
| `chunking.py` | 청크 생성 + **메타데이터 부착** |
| `index.py` | 임베딩(`text-embedding-3-small`) → **Chroma** 구축 |

**청크 2종:**
- **A. 문항-서술 청크**: 문항 블록 텍스트(질문+서술). 자연어 질의 검색용.
- **B. 정형-사실 청크**: std_id 그룹을 사람이 읽는 표 문장으로 렌더(예 "2023 확대희망 친환경제품: 보일러 6.1%, 태양광 5.0% …"). "1위가 뭐냐" 류 검색용.

**청크 메타데이터(CLAUDE.md 필수 + 확장):**
`source, page, parser_type, chunk_id, token_count, warning` **+ `year, std_id, figure/table id, value_provenance`**.
→ 검색 결과에서 바로 출처를 인용할 수 있게.

**저장소**: Chroma(로컬, `outputs/chroma/`). 의존성 `uv add chromadb`.

---

## 5. 검색·근거 답변 레이어 (4) — grounding (신규)

| 모듈/역할 | config 상수 |
|---|---|
| `retriever.py` 벡터 top-k (+키워드 하이브리드 옵션) | `EMBEDDING_MODEL` |
| 질문 재작성 | `REWRITE_MODEL` |
| 재정렬(rerank) | `RERANKER_MODEL` |
| 답변 생성(인용 강제) | `ANSWER_MODEL` |
| 예시 질문 | `EXAMPLE_Q_MODEL` |

**답변 계약(환각 차단):**
- 입력: 사용자 질문 + **검색된 청크들(출처 메타 포함)만**.
- 출력: 답변 + **각 주장에 `[source p.N]` 인용**. 청크에 없으면 **"문서에서 찾을 수 없습니다"**.
- 수치는 정형 DB(B청크) 우선. (값 신뢰성 ↑)

`app.py`: 기존 "문서 Q&A" 탭을 **이 RAG 파이프라인으로 교체**(현재 통째-프롬프트 Baseline 폐기). "검수" 탭은 유지.

---

## 6. 검수 레이어 (5) — 사람 (기존 5단계)

- `review.py` + `corrections.py` + app.py 검수 탭(완성됨).
- **이 레이어가 "추측 격리"의 핵심**: 비전 불일치·플래그·저신뢰가 모두 여기로 모이고, 사람이 출처 보고 확정한 것만 데이터가 됨.

---

## 7. 파일 구조 (최종)

```
app.py                  # Q&A(RAG) 탭 + 검수 탭
rag/
  config.py             # 모델 중앙관리 (있음)
  ingestion.py          # 0 진단 (있음)
  parsing.py            # 1 블록분리 (있음)
  extract.py            # 2 텍스트 추출 (있음)
  extract_vision.py     # 2v 비전 추출 (있음)
  refill_vision.py      # 빵구 비전 재추출→검토후보 (있음, 정책 보수화)
  standardize.py refine.py dedup.py flags.py review.py   # 3~4 정형·정제 (있음)
  corrections.py        # 5 보정 I/O (있음)
  chunking.py           # 6.1 청킹+메타 (신규)
  index.py              # 6.2 임베딩·Chroma (신규)
  retriever.py          # 6.3 검색 (신규)
  answer.py             # 6.6 근거 답변 (신규; 또는 app.py 내)
eval/                   # 6.8 평가 질문 (신규)
outputs/                # 산출물 + chroma/
```

---

## 8. 구축 순서 (제안)

1. **파싱 충실화 마무리**: 추출에 provenance(figure/page/구절) 강화 + refill 정책 보수화(판단→검토큐).
2. **chunking.py**: A/B 청크 + 메타데이터.
3. **index.py**: Chroma 임베딩 인덱스.
4. **retriever.py**: top-k 검색(+rerank).
5. **answer.py / app.py**: 근거 인용 답변으로 Q&A 탭 교체.
6. **eval/**: 평가 질문으로 검색·인용 품질 점검.

각 단계는 독립 실행·검증 가능(CLAUDE.md: 단계별 구현). 의존성: `chromadb`.

---

## 9. 미해결/결정 필요

- **임베딩 대상 언어**: 한국어 문항 — `text-embedding-3-small`로 충분한지 평가셋으로 확인(부족 시 교체 검토).
- **하이브리드 검색**: 1차는 벡터만, 키워드(BM25) 결합은 평가 후.
- **refill 휴리스틱 처리**: 원칙상 자동 덮어쓰기를 더 줄이고 검토큐로 보낼지 — 사용자 확인 필요.
