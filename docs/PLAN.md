# k-green-signal 진행 계획 (PLAN.md)

> 프로젝트: **대한민국 친환경 소비 인지도 실시간 신호등 (k-green-signal)**
> 목표: 「친환경 생활·소비 국민 인지도 조사」 결과보고서(총 14개년: 2007, 2013~2025)를
> **근거 기반 정형 데이터셋(Long-format)** 으로 통합하고, 그 위에 근거 인용 질의응답을 올린다.
> 추출 범위는 우선 **'전체(국민 전체)' 핵심수치만** (하위집단 교차표는 추후).
>
> 갱신: 2026-06-26 · 현재 완료: **2023·2024·2025 0~5단계**.
> dedup 후 통합 CSV 858행, 표준문항 104개, 검수 큐 289행.
>
> **완료**: 0~4단계(정제) · **5단계 검수 UI**(`app.py` 검수 탭 + `corrections.jsonl`, 다중 업로드) ·
> **비전 추출**(`extract_vision.py`, 깨진 표 복원 → 검토 후보 라우팅) ·
> **6단계 RAG 기본**(청킹→Chroma 인덱싱→검색→근거 인용 답변, `app.py` RAG 탭).
>
> **앱 재설계 완료(2026-06-26)**: `app.py`를 **가이드 스텝퍼**(업로드→인제스트→검수→(엄격 게이트)→인덱싱→**단일 RAG Q&A**)로 재작성. 🩺 시스템 로그 패널·단계별 진행/소요시간·데이터 상태 패널. 검증 하네스: `rag/core/logging_setup.py`·`rag/curate/validate.py`·`rag/pipeline.py` + **Playwright `tests/e2e` 7 passed**. (`RAG_FAKE_LLM`로 결정적). 상세 로그는 [`LOGGING.md`](./LOGGING.md).
>
> **정합성·구조 강화(2026-07-02)**: 멀티 에이전트 정합성 리뷰로 결함 6건 수정(비전 검수 우회·integrate 게이트 우회·연도 오인·index None 크래시·출처 페이지·dedup 드롭) + 재현 테스트 8건. `refine` 스레드풀 병렬화. 폴더 재구성(`docs/`·`curation/`), 로거 표준화, `main.py` 삭제. **`rag/`를 서브패키지(`core/ingest/transform/curate/retrieval`)로 재편**(실행 `python -m rag.<pkg>.<mod>`). 단위 **52 passed**, Ruff 0.
>
> ### 설계 원칙: "추측은 데이터가 아니다"
> 문서에 실제 있는 것만 출처와 함께 DB에. LLM·휴리스틱의 불확실 판단은 검토 후보로만(자동 반영 금지),
> 사람이 출처 보고 `corrections.jsonl`로 확정. 답변은 grounding에 근거(근거 없으면 "찾을 수 없음").
>
> **6단계 세부**:
> - [x] 6.1 `chunking.py` — (year, std_id) 단위 사실 청크 + **방법론 지식청크**(`parser_type="methodology"`), 출처 메타 + corrections 반영. **사실 196 + 방법론 지식 6 + 외부 맥락 16 = 218 청크**(게이트는 사실 청크만 검사).
> - [x] 6.2 `index.py` — `text-embedding-3-small` → Chroma(`outputs/chroma/`, cosine).
> - [x] 6.3 `retriever.py` — 벡터 top-k 검색(연도·std_id·**parser_type 필터**), 유사도·출처 반환.
> - [x] 6.6 `answer.py`/app.py — 검색 청크만 근거로 **출처 인용** 답변, 없으면 "찾을 수 없음". **두 모드**(`cite` 사실 인용 · `advise` 데이터 기반 제언: 다면검색+KEEP/ADD/DROP/FIX+방법론 주석 반영) + **상세도**(요약/표준/상세).
> - [x] 6.5 rerank(`RERANKER_MODEL`, listwise) — 정답 청크 5위→1위 확인. + 순위 질문 시 집계항목(기타/없음/무응답) 제외하도록 `answer.py` 프롬프트 보강.
> - [x] 🚦 신호등 의사결정 프레이밍(`signals.py`+`ui/signal.py`) — 헤드라인 '주목할 실제 변화'(2024→2025 설계 동일 구간만), 2023→2024 개편·척도 변경은 '⚠️ 해석 유의' 분리. 집계·비응답·이진 상보 중복 제외. 차트 연도축 ordinal 전환.
> - [x] `app.py` → `ui/` 모듈화 완료 — 단계·탭 화면을 `ui/`로 전량 추출(`signal` 895·`review` 426·`ingest` 355·`rag` 107·`index` 56·`common` 18행). **app.py 1935→309행**(셸: 라우팅·스텝퍼·상태/로그 패널). 한 모듈씩 ruff·테스트 검증(68 passed).
> - [x] 6.4 질문 재작성(`REWRITE_MODEL`) — `rewrite_query()`, 검색어만 정규화·확장(recall↑), RAG 탭 체크박스.
> - [x] 6.7 예시 질문(`EXAMPLE_Q_MODEL`) — `suggest_questions()`, 실데이터 기반 추천 질문(RAG 탭 클릭→채움).
> - [x] 6.8 `eval/` — 검색·인용 품질 평가셋(`questions.jsonl` gold 6케이스 + `run_eval.py` 채점 러너). 케이스 확충은 백로그.
> - [ ] (연계) 검토 후보(`vision_candidates.csv`)를 검수 탭에 노출 + 문항-서술 청크 추가
>
> ### ⚠️ 재점검 기반 다음 할 일 (2026-06-26) — 상세는 [`LOGGING.md`](./LOGGING.md)
> **결정 먼저(범위)**: 스텝퍼 재설계가 원래의 작은 요청(업로드 가시화·탭 통합)보다 과했음. (a)현 스텝퍼+실제검증 / (b)최소안 축소 / (c)절충 중 **사용자와 합의 후** 진행. 그 전엔 신규 기능 추가 금지.
> 우선순위 백로그:
> - **A(최우선)** 인덱스–게이트 정합: 현재 라이브 인덱스가 게이트 미통과 데이터(미확정 비전38·미검수 high33·빈값)로 구축됨 → 검수 확정 후 재인덱싱(또는 "미통과 인덱스" 경고 표시). 원칙 위반 해소.
> - **C** 실제 검증: E2E가 `RAG_FAKE_LLM`이라 UI 배선만 증명. 🩺 로그 패널·실제 검색/답변·실제 인제스트를 실제 LLM(`@slow`)/수동으로 검증 + `eval/` 회귀 안전망.
> - **B** 인제스트 견고화: happy-path·에러 복구·`state.json` 영속(새로고침 복구) 미구현.
> - **D** 범위 완성: 빈칸5·옛 연도(2007,2013~2022)·"실시간 신호등" 시각화 미착수.

### 🔜 다음 할 일 (2026-07-03) — 커밋 게이트 통과 후
- **커밋/푸시**: 이번 세션 산출물 + `samples/`(Option A: CSV·청크·Chroma 포함, PDF 제외)을 로컬 커밋 → `/ship`으로 푸시. PDF는 `.gitignore: samples/data/*.pdf`가 자동 제외. (정책: [`DECISIONS.md` D15](./DECISIONS.md))
- **최우선(근본 해금)** — **2014~2022 옛 보고서 인제스트**. 변곡점 해석의 천장을 올리는 유일한 근본 해법(현재 2023~2025 3개년, 깨끗한 전이는 2024→2025 하나뿐이라 외부 맥락 대조가 얕음). 실제 착수는 옛 KEITI·환경부 보고서 **확보 가능 여부에 gated**(사용자 확인 대기). ← D14·백로그 D의 선결 과제.
- **알려진 데이터 이슈(소규모)** — 복원 경로(`confirmed_only_rows`)가 살짝 다른 키로 기존 라벨을 재추가해 **청크 3/196에 중복 응답라인**(예: '구매 의향 있음: 96.0%' 2회). 검색엔 무해하나 정합성 차원 정리 대상(복원 전 full-key 정규화 검토).
- **eval 확충** — gold 6케이스에 **외부 맥락·advise 상황 해석** 회귀 케이스 추가(6.8 백로그).

---

## ✅ 완료 (0~3단계 + 모델 설정)

- [x] **0 진단** `rag/ingestion.py` — 5개 모두 digital-text(OCR 불필요). 통계는 표가 아니라 서술문 인라인 수치 + 차트 이미지.
- [x] **1 블록분리** `rag/parsing.py` — 285 문항 블록. 신호: `Q.` / `섹션 N.` / `N)` / `(N=…)`·`[BASE…]` / `<그림·표 X-Y>`.
- [x] **2 LLM 추출** `rag/extract.py` — Structured Outputs(strict). `outputs/*.extracted.jsonl` (285건).
- [x] **3 표준화** `rag/standardize.py` — 배치 누적 사전. `outputs/question_dictionary.json`, `outputs/standardized_long.csv`.
- [x] **모델 중앙화** `rag/config.py` — 생성/추출/표준화 `gpt-5.4-mini`, 임베딩 `text-embedding-3-small`.
- [x] **2024·2025 데이터** — 각각 추출(2024 62블록 전부 high / 2025 55블록 high53·med2) + **2024+2025 표준화**(표준문항 65개, 51개가 2개년 연결, CSV 503행).

---

## 🔜 4단계: 데이터 정제·통합  (다음, 최우선 / 엄밀히 정의됨)

> 산출 목표: 신뢰할 수 있는 `outputs/standardized_long.clean.csv`

### 4.1 응답 라벨 표준화 (`rag/refine.py`) ✅ 완료 (2024·2025)
- [x] 4.1.1 표준문항(std_id)별로 등장한 응답 라벨 전부 수집
- [x] 4.1.2 LLM(`STANDARDIZE_MODEL`)으로 동의 라벨 묶기 — **문항(std_id) 단위 호출**. 긍정/부정 반의어 묶기 금지 규칙 강화(초기에 `알고있음`↔`모름` 오묶음 1건 발견→프롬프트 보강 후 0건)
- [x] 4.1.3 라벨 사전 저장 `outputs/response_label_map.json` (`{std_id: {원본라벨: 대표라벨}}`)
- [x] 4.1.4 `outputs/standardized_long.clean.csv` 에 `std_response_label` 컬럼 추가 (원본 `response_label`·`standardized_long.csv` 보존). **3개년 재실행: 861행, 131개 라벨 통합.** (개방형 문항 `친환경제품_확대희망품목` 과잉병합 발견 → 프롬프트에 "다른 품목은 합치지 말 것" 규칙 추가 후 해소.)

### 4.2 중복 제거/분리 (`rag/dedup.py`) ✅ 완료  (※ 2023은 3종이 아니라 단일 통합 PDF였음)
> 중복은 3종류였음(검수 후 정정 — "순위형"은 오진단이었고 실제론 라벨 과잉병합):
> **A. 문항 과잉병합** — `환경표지`(에코라벨) vs `환경성적표지`(EPD)를 한 std_id로 묶음. subsection 키워드로 분리.
> **B. 라벨 과잉병합** — 4.1이 서로 다른 보기를 한 라벨로(예: 명칭선호의 친환경표지(마크)/환경마크/현행법적명칭). 원래 라벨로 un-merge.
> **C. 진짜 중복** — 같은 보기 두 번(값 동일/한쪽 빈칸, 예: 대형마트 30.6/30.6). 값 있는 행 1개만 남기고 제거.
> B/C 구분: 같은 블록 중복행의 값이 다르면 B(분리), 같거나 한쪽뿐이면 C(제거).
- [x] 4.2.1 중복 탐지: 같은 `(year,std_id,std_response_label)` (블록키로 같은블록/다른블록 구분)
- [x] 4.2.2 A=subsection 키워드 분리(`SPLIT_RULES`) / B=un-merge / C=값 있는 행만 유지. **결정: A·B는 분리, C만 제거**(사용자 승인).
- [x] 4.2.3 `outputs/standardized_long.dedup.csv`(858행) + `outputs/dedup_log.csv`. split 17/un-merge 9/drop 3. 남은 중복키 0.
- 산출: `dedup.csv`가 4.3의 새 입력(flags.py가 dedup 있으면 우선 사용). 분리 std_id: `환경표지_구매유도요인`·`환경표지_우선구매이유`(2024·2025), `친환경소비_포인트적립_희망품목`(2023).

### 4.3 의심값 자동 플래그 (`rag/flags.py`) ✅ 완료 (2023·2024·2025, dedup 후 재실행)
- [x] 4.3.1 전년 대비 급변(`JUMP_PP=20`%p) → `flag_jump`. (**39행**). `prev_value`/`yoy_delta` 컬럼도 추가.
- [x] 4.3.2 `prev_year_note` 정합성 → `flag_mismatch` (+`mismatch_verdict`/`mismatch_reason`). **LLM 판정**(노트가 자유서술이라). 노트는 '전년→보고연도' 변화 설명이므로 **직전 연도 값이 데이터에 있을 때만 검증**. (**33행 모순**; dedup 분리/un-merge로 가짜 모순 일부 해소. 2023 추가로 2024 노트도 검증가능해짐.) 입력은 dedup.csv 우선.
- [x] 4.3.3 합계 검증: 단일응답(multi_response=False)+`unit='%'` 그룹 합이 100±`SUM_TOL=5` 밖이면 `flag_sum_violation`+`sum_total`. (**3개년 134행**; 2023이 누적라벨 `관심있음(1+2)`·복수응답 오분류로 다수) — multi_response 오분류·보기 누락을 잘 포착.
- 산출: `outputs/standardized_long.flagged.csv` (clean CSV + 플래그 컬럼). 원본 보존.

### 4.4 저신뢰 검수 큐 (`rag/review.py`) ✅ 완료 (2023·2024·2025)
- [x] 4.4.1 `extraction_confidence ∈ {low,medium}` · `warning` · 4.3 플래그(jump/mismatch/sum) · **중복키**(같은 `(year,std_id,std_response_label)`) 중 하나라도 해당하는 행 추출
- [x] 4.4.2 `outputs/review_queue.csv` 저장 — `review_priority`(high/medium) + `review_reasons` + `source_locator`(PDF+페이지) 부여. **dedup 후 289행**(high 71/medium 218, 중복키 사유 0). 입력 `flagged.csv` 보존.

---

## 5단계: 검수 UI (Streamlit)  (진행 중 — 5.1~5.3 완료)

- [x] 5.1 `app.py`에 "🔍 검수" 탭 추가 (기존 Q&A와 `st.tabs`로 분리). Q&A는 API Key 필요, 검수는 Key 없이도 동작.
- [x] 5.2 `review_queue.csv`(289행)를 `st.dataframe`(single-row 선택)으로 표시 → 행 선택 시 상세 패널(문항요약·값·플래그 풀이·`source_locator`). **단 원본 PDF 페이지 렌더링은 아직 텍스트 위치(파일+페이지)만**; 이미지 표시는 추후.
- [x] 5.3 사람이 값 확인/수정 → `rag/corrections.py`가 `outputs/corrections.jsonl`에 한 줄 append. status=fixed/confirmed/skip. 이미 검수한 행은 표에 ✅. 같은 행 재수정 시 최신이 이김.
- [~] 5.4 재정제 반영: `corrections.apply_corrections(rows)` 함수는 완성(fixed만 덮어쓰고 원본 보존). **파이프라인 연결은 미완** — 검수 데이터가 쌓인 뒤 refine/flags 입력단에 끼울 예정.

> 모듈: `rag/corrections.py`(stdlib만; row_key=(year,std_id,std_response_label)+field) · `app.py`(load_review_queue 캐시, render_review_tab/render_detail_and_edit/render_qa_tab + tabs main).

---

## 6단계: RAG 검색·질의응답 시스템 (CLAUDE.md 파일 구조 준수)

> 정제된 데이터셋/원문 위에 자연어 질의응답을 올린다. 모델은 `config.py` 역할별 상수 사용.

- [x] 6.1 **청킹** `rag/retrieval/chunking.py` — (year, std_id) 사실 청크 + 방법론 지식청크. metadata 필수항목 유지(source/page/parser_type/chunk_id/token_count/warning)
- [x] 6.2 **임베딩·인덱싱** `rag/retrieval/index.py` — `EMBEDDING_MODEL`(text-embedding-3-small) + Chroma 벡터DB(`outputs/chroma/`, cosine)
- [x] 6.3 **검색** `rag/retrieval/retriever.py` — 벡터 유사도 top-k 검색(연도·std_id·parser_type 필터)
- [x] 6.4 **질문 재작성** — `answer.rewrite_query()`(`REWRITE_MODEL`): 질의 정규화/확장으로 recall↑. 검색어만 재작성하고 연도·라우팅·표시는 원 질문 유지. `answer(rewrite=True)` + RAG 탭 체크박스(`Answer.rewritten`로 투명 노출). FAKE·실패 시 원문 폴백.
- [x] 6.5 **Reranker** — `RERANKER_MODEL`로 검색 결과 재정렬(`retriever._rerank`, listwise)
- [x] 6.6 **답변 생성** — `rag/retrieval/answer.py`, `ANSWER_MODEL`, 근거(출처·연도) 인용. 두 모드(`cite`·`advise`) + 상세도(요약/표준/상세)
- [x] 6.7 **예시 질문** — `answer.suggest_questions()`(`EXAMPLE_Q_MODEL`): 인덱싱된 실제 문항명을 씨앗으로 '답할 수 있는' 추천 질문 생성(RAG 탭 클릭→질문칸 채움). FAKE·실패 시 정적 폴백.
- [x] 6.8 평가셋 `eval/` — `eval/questions.jsonl` + `eval/run_eval.py`(검색·인용 품질 점검)

---

## 7단계: 하위집단 교차표 추출 (보류 — 결정: '전체만' 유지)

> **결정(2026-06-25): 전체만 + 본문 서술 추출 유지. 하위집단은 당분간 안 함.**
>
> 참고로 하위집단(성별·연령·지역…) 데이터는 두 경로가 있다:
> - (권장) **부록 통계표** — 예: 2025 PDF p.121~144에 전체 교차표가 깔끔한 표로 존재.
>   `Docling`(TableFormer)으로 셀 구조까지 정확히 복원됨을 검증 완료. Vision 불필요.
>   Windows에선 `HF_HUB_DISABLE_SYMLINKS=1` 필요(심볼릭 링크 권한 오류 회피).
> - (대안) 본문 차트 이미지(`<그림>`)를 `VISION_MODEL`로 판독.
>
> 나중에 하위집단이 필요해지면 **Docling 부록표 경로**를 우선 검토. (`uv add docling`)

- [ ] 7.1 (보류) Docling으로 부록 통계표 → 구조화 표
- [ ] 7.2 (보류) long CSV에 `subgroup_type`/`subgroup_value` 차원 추가

---

## 8단계: 14개년으로 확장

- [ ] 8.1 옛 PDF 확보 (2007, 2013~2022) → **결정 필요(확보 경로)**
- [ ] 8.2 연도별 형식 차이 점검(메타 4번째 형식 등) → parsing 규칙 보강
- [ ] 8.3 동일 파이프라인(1→2→3→4) 적용, 연도 누적
- [ ] 8.4 표준 문항 사전에 옛 연도 매핑 (사전은 누적되므로 일관 유지)

---

## ❓ 사용자 결정이 필요한 항목 (이게 정해져야 위 단계가 '엄밀'해짐)

1. ~~**4.2.2 2023 중복 우선순위**~~ → **무효화**: 2023은 통합본/주제별본 분절이 아니라 단일 PDF였음. 실제 중복은 (A)문항 과잉병합·(B)라벨 과잉병합·(C)진짜중복이었고 **A·B 분리 + C 제거**로 처리 완료(2026-06-26).
2. ~~**7 하위집단 진행 여부**~~ → **결정됨: '전체만' 유지, 본문 서술 추출만 진행** (2026-06-25)
3. **8.1 옛 PDF 확보 경로**: 2007·2013~2022 보고서 원본을 어디서 받을지
