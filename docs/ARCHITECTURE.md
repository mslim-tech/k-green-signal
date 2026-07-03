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
   │           [RAG 답변]  ui/rag.py 💬 질의 모드  ← page/구절 인용. 근거 없으면 "없음"
   │
   ▼  (5) 검수 (사람 + LLM 검증)
[보정]  corrections.jsonl                       ← 사람 확정 + LLM 원문 재판독 확정(llm_verified)
                                                   — 같은 행에 사람 기록이 있으면 항상 사람이 우선
```

**불변 규칙(원칙 구현):**
- (2) 정형 DB에는 **충실 추출값만** 들어간다. 비전/휴리스틱의 불일치·추측은 (5) 검토큐로.
- (4) 답변은 (3)에서 **검색된 청크만** 근거로 쓰고, 모든 주장에 **출처(source+page)** 를 붙인다. 검색 결과에 없으면 답을 지어내지 않는다.
- 제(어시스턴트)의 휴리스틱 판단(퍼지매칭·자동 덮어쓰기)은 데이터에 직접 쓰지 않는다 → `discrepancy`로 검수.

**코드 조직 (2026-07-02):** 위 레이어는 `rag/` 서브패키지로 1:1 매핑된다 — `core/`(config·paths·logging_setup) · `ingest/`(파싱·추출·비전) · `transform/`(표준화·정제·검수큐) · `curate/`(corrections·refill_vision·integrate·validate) · `retrieval/`(청킹·인덱싱·검색·답변), 그리고 최상위 `signals.py`(🚦)·`pipeline.py`. 각 단계는 `python -m rag.<pkg>.<mod>`로 독립 실행하며 `pipeline.py`가 이를 서브프로세스로 오케스트레이션한다. **게이트(`curate/validate.py`)는 side-channel(예: `integrate_oldyears`)로 clean/dedup에 직접 기입돼 검수 큐를 건너뛴 불확실 행까지 차단한다(검사 #5).**

---

## 2. 파싱 레이어 (1) — 충실 추출 + 출처

| 모듈 | 역할 | 상태 |
|---|---|---|
| `parsing.py` | 본문을 문항 블록으로 분리(서술형 수치) | 있음 |
| `extract.py` | 블록 텍스트 → LLM 구조화 추출 | 있음 |
| `extract_vision.py` | **표 블록: 페이지 이미지 → 멀티모달 추출** | 있음(신규) |
| `refill_vision.py` | 빵구 블록 비전 재추출 → **검토 후보 생성**(`vision_candidates.csv` 전용, 자동 데이터 반영 없음) | 있음 |

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

**청크 4종(사실 2종 + 지식 2종):**
- **A. 문항-서술 청크**: 문항 블록 텍스트(질문+서술). 자연어 질의 검색용.
- **B. 정형-사실 청크**: std_id 그룹을 사람이 읽는 표 문장으로 렌더(예 "2023 확대희망 친환경제품: 보일러 6.1%, 태양광 5.0% …"). "1위가 뭐냐" 류 검색용.
- **C. 방법론 지식 청크(`parser_type="methodology"`)**: 큐레이션된 '비교 유의' 지식(연도 간 척도·정의가 바뀐 지표 — 예: 인지도 문항이 2023 4점척도→2024~ 2점척도). `curation/methodology_notes.json`이 단일 소스이고 `rag/curate/methodology.py`가 로드하며, `chunking.build_knowledge_chunks()`가 지식청크로 만든다. **사실 청크(A·B)와 함께 인덱싱되지만 `parser_type`로 명확히 구분**되어, RAG(특히 '데이터 기반 제언')가 척도 변경 아티팩트를 실제 추세로 오독하지 않도록 근거로 쓰인다. → "추측은 데이터가 아니다"의 확장: **사람이 출처와 함께 확정한 해석 지식**은 종류가 분명하면 인덱싱 대상이 된다.
- **D. 외부 맥락 지식 청크(`parser_type="external_context"`)**: 큐레이션된 '그해 뉴스·사회적 사건'(예: 2023 그린워싱 적발 급증·가이드라인 발간). `curation/external_context.json`이 단일 소스, `rag/curate/external_context.py`가 로드, `chunking.build_external_context_chunks()`가 지식청크로 만든다. RAG(특히 advise)가 **데이터 변화를 그해 사건과 대조해 '상황 적응형 해석'**을 하게 한다 — 프롬프트가 **'상관·맥락일 뿐 인과가 아님'을 강제**하고 사건을 [출처]로 인용한다. 대시보드 '변곡점 × 외부 맥락' 패널도 같은 파일을 읽는다(단일 소스).

**청킹 API**: `build_chunks(rows)`=사실 청크(A·B), `build_knowledge_chunks()`=방법론(C), `build_external_context_chunks()`=외부 맥락(D), `build_all_chunks()`=A+B+C+D(=인덱싱 대상). **준비 게이트(`curate/validate.py`)는 사실 청크만 검사**하므로 지식청크가 '빈 청크'로 오인되지 않는다.

**현재 인덱스 규모**: 사실 196 + 방법론 지식 6 + 외부 맥락 16 = **218 청크**.

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
- **검색은 벡터(임베딩) 단일** — 키워드(BM25) 하이브리드는 도입하지 않음. `retriever.search()`는 `year`·`std_id`(라우팅)에 더해 **`parser_type` 필터**를 받아 특정 청크 종류(예: 방법론 지식청크)만 좁혀 뽑을 수 있다.

**두 답변 모드 + 상세도** — `answer(query, k, year, mode, detail)`:
- **`mode="cite"`(사실 인용, 기본)**: 검색 청크의 사실만 출처와 함께 답한다.
- **`mode="advise"`(데이터 기반 제언)**: `_advise_retrieve`로 **다면 검색** — ①추세(질문 그대로) ②장벽/개선 ③**방법론 지식청크(`parser_type="methodology"`)** ④**외부 맥락 지식청크(`parser_type="external_context"`)** 네 축을 `chunk_id`로 병합·중복제거해 모은다. 프롬프트가 **KEEP/ADD/DROP/FIX** 구조를 강제하고, 💡제언(결론)을 위에·📊근거 사실을 아래에 두며, 문서상 척도 변경은 '실제 추세'가 아니라 **FIX(척도 표준화) 대상**으로 지목하고, 외부 맥락이 있으면 **데이터 변화를 그해 사건과 엮어 '상황 해석'**을 덧붙이되 **상관·인과를 구분**하게 한다.
- **상세도 `요약 / 표준 / 상세`(`DETAIL_GUIDE`)**: 같은 근거로 서술 길이·깊이만 달리한다(프롬프트 지침만, 토큰 상한 변경 없음). 두 모드 공통.

**앱 구조(2026-07-03 "결과 먼저, 관리 나중" 개편)**: `app.py`는 3모드 — **🚦 대시보드**(정형 CSV가 있으면 랜딩, 키·인덱스 불필요) · **💬 AI에게 묻기**(위 RAG 파이프라인, 키 필요) · **🛠 데이터 준비**(업로드→인제스트→검수→인덱싱 4단계 게이트 스텝퍼). 질의 화면은 advise 답변을 헤딩 계약(`#### KEEP/ADD/DROP/FIX`) + `parse_advise_sections`로 **갈래별 카드로 구조화**(파싱 실패 시 원문 폴백 — 구조 합성 없음)하고, 출처는 카드 + **온디맨드 원문 페이지 토글**(PDF 있을 때만)로 보여주며, 같은 입력의 답변은 세션에 캐시해 재생성(과금)을 막는다.

---

## 6. 검수 레이어 (5) — 하이브리드(사람 + LLM 검증)

- `rag/transform/review.py`(큐 생성) + `rag/curate/corrections.py`(기록) + `ui/review.py`(화면, 데이터 준비 3단계).
- **이 레이어가 "추측 격리"의 핵심**: 비전 불일치·플래그·저신뢰가 모두 여기로 모인다. 확정 경로는 둘:
  - **사람 검수** — 표(브라우즈) 또는 **순차 검수 모드**(저장하면 자동으로 다음 미검수 행). 검수 상세에 **원문 PDF 페이지 미리보기**(extract_vision 렌더러 재사용, PDF 없으면 폴백)를 렌더해 "사람이 원문 보고 확정"을 앱 안에서 완결. 상태 라디오 기본값은 비변조인 '원래 값 맞음'.
  - **LLM 검증(`rag/curate/adjudicate.py`)** — 게이트가 차단하는 '불확실 high'를 원문 페이지 비전으로 독립 재판독해 agree/correct만 `status=llm_verified`로 확정, uncertain·빈 값 지지는 에스컬레이션(값을 쓰지 않음). **사람 우선 가드**: 실행 중 사람이 같은 행을 검수하면 호출 전·쓰기 직전 이중 재확인으로 건너뛴다.
- 비전 후보의 **기각은 `confirmed`(원래 값 유지)로 기록** — `skip`으로 기록하면 chunking이 그 행 자체를 인덱스에서 제외해 버리기 때문(조용한 데이터 소실 방지).
- UI 는 `llm_verified`를 fixed 와 같은 규칙으로 반영(`effective_value`/`needs_value`) — 인덱싱(`apply_corrections`)과 화면이 같은 값을 본다.

---

## 6.5 신호등 레이어 — 의사결정 프레이밍 (`signals.py` + `ui/signal.py`)

`signals.py`는 LLM 없는 순수 함수로 정형 사실 행을 (문항, 응답라벨)별 연도 시계열로 묶어 최신 YoY(%p)로 🟢상승/🟡보합/🔴하락을 매긴다(색은 방향만, 추정/보간 없음). 여기에 **의사결정용 정직성 규칙**을 얹었다:
- **집계·비응답 라벨 제외**(`is_aggregation_label`): 기타·소계·합계·무응답, 단독 없음/모름은 신호에서 뺀다.
- **척도 변경 경계 보류**(`Series.caveat_break`): 방법론 주석이 있는 std_id의 **2023→2024** 전이는 설문 개편 영향일 수 있어 신호를 보류한다(RAG 방법론 주석과 일관).
- **이진 상보 중복 제거**(`Indicator.is_binary_mirror`): 인지/비인지처럼 합이 ~100인 거울상 라벨은 대표 1개만 센다.
- **이례적 급변 분리**(`LARGE_YOY_PP=15`): 설계 동일 구간이라도 단년 |Δ|가 15%p를 넘으면 설문 변경 가능성이 있어 헤드라인에서 빼고 '검증 필요'로 돌린다. 근거는 실측 — 설계 동일(2024→2025) 신호의 |Δ| 중앙값 6.6%p, 15%p 초과는 상위 10%(실제 변화라고 단정하지 않되 원문 확인 전엔 헤드라인 안 함).
- 신규 API: `signaled_movers()`(실제 변화만), `caveat_breaks()`(해석 유의), `Series.spans_scale_break`·`Series.is_aggregate`.

**대시보드(`ui/signal.py`)** — **앱의 랜딩 화면**(정형 CSV가 있으면 앱을 열자마자 표시 — "결과 먼저"). 의사결정용 **3단 분리**로 오독을 막는다: **🟢 "📊 주목할 실제 변화"**(설계 동일 2024→2025 + 단년 크기 정상 |Δ|≤15%p → 바로 판단) · **🔶 "큰 변화지만 검증 필요"**(설계 동일이나 |Δ|>15%p → 원문 확인 후) · **⚠️ "해석 유의"**(2023→2024 개편·문서상 척도 변경 → 실제 변화 아닐 수 있음). 카드는 테두리 컨테이너에 지표명·응답라벨을 **잘림 없이 전부** 보여준다(`_mover_card`). **"💡 2026 설문 설계 제언 받기"** 버튼이 advise 모드로 연결한다. 단일 연도만 조사된 문항(판단 기준·구매 장벽)은 추세 대신 **그 해 스냅샷(빈도순 파레토)**으로 보여주고 단일 연도임을 명시한다(`_render_single_year_snapshot`). 추세 차트는 **연도 축을 quantitative→ordinal(`연도:O`)로 바꿔** 중복 눈금 렌더링을 고치고, 없는 연도는 선을 끊어(null 갭 — 가짜 보간 없음) 범례 라벨도 잘리지 않게(`labelLimit=0`) 했다.

---

## 7. 파일 구조 (최종)

> §1의 "코드 조직 (2026-07-02)"대로 `rag/`는 서브패키지 구조다. 실행은 `python -m rag.<pkg>.<mod>`.

```
app.py                  # 진입점 셸(355행): 3모드 라우팅(🚦 대시보드 랜딩/💬 AI에게 묻기/🛠 데이터 준비)·상태/🩺 로그 패널 + main()
ui/                     # app.py 에서 추출한 UI 패키지 (모드·단계 화면 전량 분리)
  signal.py             #   🚦 신호등 대시보드(901행) — 랜딩 화면(의사결정 프레이밍)
  review.py             #   데이터 준비 3단계 검수(555행) — 순차 모드·원문 페이지 + 비전 후보 + LLM 검증(adjudicate)
  ingest.py             #   데이터 준비 1·2단계 업로드·인제스트(356행) + 진행 모니터
  rag.py                #   💬 AI에게 묻기(202행) — 사실 인용/제언 카드 · 상세도 · 출처 카드
  index.py              #   데이터 준비 4단계 인덱싱(58행, 준비 게이트)
  common.py             #   공유 상수·경로(22행 — OUTPUT_DIR 경유 REVIEW_QUEUE_PATH 등)
curation/               # 사람 큐레이션 참조 데이터(설정 아님, 커밋됨)
  methodology_notes.json #   방법론 '비교 유의' 지식(척도 변경 등) → parser_type='methodology' 로 인덱싱
  external_context.json  #   외부 맥락(정책·사건) → parser_type='external_context' 로 인덱싱 + 신호등 패널
  mapping_review.csv     #   과병합 교정 워크시트
rag/
  core/                 # config(모델 중앙관리)·paths(경로 단일화)·logging_setup
  ingest/               # 0~2 ingestion·parsing·extract·extract_vision(+oldtable)
  transform/            # 3~4 standardize·std_aliases·refine·dedup·flags·review
  curate/               # corrections·refill_vision·adjudicate·validate·methodology·external_context·integrate_oldyears
  retrieval/            # 6 chunking·index·routing·retriever·answer
  signals.py            # 🚦 신호등 — 연도 추세 신호(순수 함수)
  pipeline.py           #    인제스트 단계를 python -m 서브프로세스로 실행+로그 캡처
eval/                   # 평가 골드 9케이스(cite 6 + advise 3) + run_eval.py 채점 러너
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
- ~~**refill 휴리스틱 처리**~~ → 해소(후보 전용으로 확정): refill 은 `vision_candidates.csv`만 쓰고 canonical 은 건드리지 않으며, 사람이 검수 화면에서 확정/기각한다.
- **`llm_verified` 사람 승인 단계**: LLM 검증 확정이 사람 사인오프 없이 게이트를 통과·색인됨 — 승인 단계 추가 여부는 명시적 결정 필요([`PLAN.md`](./PLAN.md) '리뷰 이연 항목' ①, hybrid Stage 3 후보).
