# 작업 로그 (LOGGING.md)

> 사용자 요청과 그에 대한 작업 내용을 날짜별로 정리하는 파일.
> "내가 무엇을 요청했고, 무엇이 어떻게 처리됐는지"를 추적한다.

---

## 2026-07-03 (2) — UX/UI 개편 "결과 먼저, 관리 나중"

사용자 요청: "전체 코드베이스를 보고 시스템이 사용자 의도에 맞게 동작하도록, 더 나은 UX/UI로 개편"(계획 승인 후 4커밋 단계 진행).

### 요청 → 대응 요약

| # | 문제(의도 위반/마찰) | 대응 / 결과 |
|---|---|---|
| 1 | 앱 재인덱싱 시 advise 근거(지식청크 22개) 조용히 소실 | `ui/index.py`가 CLI 와 동일한 `build_all_chunks()`(사실+방법론+외부맥락) 사용 |
| 2 | 앱에 버튼 있는 작업을 "CLI 가서 실행" 안내(막다른 길) 2곳 · 연도 필터 하드코딩 · 검수큐 캐시 미무효화 · LLM 검증(과금) 키 게이트 밖+새로고침 시 이중 과금 | 앱 내 단계 안내로 교체 · `signals.dataset_years` 동적 파생 · mtime 캐시 키 · 키 게이트+pid 영속화 복구(`adjudicate_state.json`) |
| 3 | 신호등(키·인덱스 불필요한 최종 성과물)이 6단계 맨 뒤 — "클론 즉시 신호등"과 상충 | **3모드 IA**: 🚦 대시보드(정형 CSV 있으면 랜딩) · 💬 질의 · 🛠 데이터 준비(1~4단계 게이트 스텝퍼 유지). 제언 점프는 mode 전환으로 |
| 4 | 검수: 행마다 선택→라디오(기본값 '값 고침'=변조성)→저장 반복, 원문 PDF 는 앱 밖 | **순차 검수 모드**(저장→자동 다음 행) + **원문 페이지 미리보기**(extract_vision 렌더러 재사용·dpi150·캐시, PDF 없으면 폴백) + 안전 기본값 '원래 값 맞음' + 행 키 위젯 키(입력 잔류 방지) |
| 5 | advise 답변(KEEP/ADD/DROP/FIX)이 통짜 마크다운, 출처 클릭 불가 | 프롬프트 **헤딩 계약** + `parse_advise_sections`(합성 금지·실패 시 원문 폴백) → 갈래별 배지 카드. 출처는 카드(연도·std_id 배지·유사도)+**온디맨드 원문 페이지 토글**(PDF 있을 때만) |
| 6 | RAG·검수 E2E 부재, eval 은 cite 6케이스뿐 | E2E +3(출처 카드 폴백·advise 구조화·순차 검수), 파서 단위 3, eval advise 골드 3(척도 FIX·인과 고지) + `run_eval.py` mode 지원. **단위 56 + E2E 21 = 77 passed** |

커밋: `fix(app): align in-app actions with pipeline intent` → `feat(app): promote signal dashboard to landing with 3-mode IA` → `feat(review): sequential review mode with source-page preview` → `feat(rag): structured advise rendering and source cards`.

---

## 2026-07-03

방법론 지식 인덱싱 + '데이터 기반 제언' 모드 + 신호등 의사결정 프레이밍 세션. 척도 변경(예: 인지도 2023 4점→2024~ 2점) 아티팩트를 실제 추세로 오독하던 문제를 데이터·지식·UI 세 층에서 해소하고, `app.py`를 `ui/` 패키지로 분리 착수.

### 요청 → 대응 요약

| # | 사용자 요청 | 대응 / 결과 |
|---|---|---|
| 1 | 척도 변경을 실제 추세로 오독하는 문제 | `curation/methodology_notes.json`(사람 확정 '비교 유의' 지식, 척도 변경 6지표) + `rag/curate/methodology.py`(단일 로더: `load_notes`·`caveats_by_std_id`). 청킹·앱 캡션이 이 한 파일을 공용(드리프트 방지). |
| 2 | 그 지식을 RAG가 근거로 쓰게 | `chunking.build_knowledge_chunks()`가 `parser_type="methodology"` 지식청크 생성 → `build_all_chunks()`(사실+지식)로 **함께 인덱싱**. 게이트는 사실 청크만 검사(`build_chunks`)라 지식청크 오인 없음. **사실 196 + 방법론 6 + 외부 맥락 16 = 218 청크.** |
| 3 | '데이터 기반 제언' 모드 | `answer(mode="advise")` — `_advise_retrieve` 다면검색(추세 + 장벽/개선 + 방법론 지식청크 필터로 반드시 포함, chunk_id 병합·중복제거). 프롬프트가 KEEP/ADD/DROP/FIX 강제, 💡제언 위·📊근거 아래, 실제 파일명 인용, 척도 변경은 FIX 대상. `retriever.search`에 `parser_type` 필터 추가. |
| 4 | 답변 길이 조절 | 상세도 `요약/표준/상세`(`DETAIL_GUIDE`) — 같은 근거로 서술 길이·깊이만(프롬프트 지침, 토큰 상한 불변). cite·advise 공통. |
| 5 | 신호등을 의사결정 도구로 | `signals.py`: 집계·비응답 라벨 제외, 2023→2024 척도경계 신호 보류(`caveat_break`), 이진 상보 중복 제거(`is_binary_mirror`). 신규 API `signaled_movers`·`caveat_breaks`·`spans_scale_break`. `ui/signal.py`: 헤드라인 '📊 주목할 실제 변화'(2024→2025 설계 동일 구간만), '⚠️ 해석 유의'로 개편·척도 변경 분리, '💡 2026 설문 설계 제언 받기'→advise 연결. |
| 6 | 추세 차트 눈금 중복 | 연도 축 quantitative→**ordinal(`연도:O`)** 전환(중복 눈금 해소), 없는 연도는 선 끊김(null 갭, 가짜 보간 없음), 범례 `labelLimit=0`(라벨 잘림 해소). |
| 7 | `app.py` 비대 → 모듈화 | 단계·탭 화면을 `ui/` 패키지로 전량 추출: `signal`(895)·`review`(426)·`ingest`(355)·`rag`(107)·`index`(56)·`common`(18)행. **app.py 1935→309행**(셸: 라우팅·스텝퍼·상태/로그 패널). 한 모듈씩 ruff 0·전체 68 passed(신규 실패 0)로 동작 보존. `__file__` 상대경로 버그(외부맥락)·`logger` 누락·미사용 import까지 수정. |
| 8 | 질문 재작성(6.4)·예시 질문(6.7) | `answer.rewrite_query()`(`REWRITE_MODEL`) — 검색어만 정규화·확장(recall↑), 연도·라우팅·표시는 원 질문 유지, `Answer.rewritten`로 투명 노출, RAG 탭 체크박스. `answer.suggest_questions()`(`EXAMPLE_Q_MODEL`) — 인덱싱된 실제 문항 씨앗으로 '답할 수 있는' 추천 질문, RAG 탭 클릭→채움. FAKE·실패 시 결정적 폴백. 실 LLM 검증: '그린카드 아는 사람'→'그린카드 인지도 인지 여부 알고 있음 비율'. |
| 9 | 클론 즉시 재현(하이브리드) | 산출물(정형 CSV·청크·**Chroma 인덱스**)을 **작업 폴더와 분리해 `samples/`에 커밋**(재사용자 충돌 0). 원본 PDF는 용량상 제외(`samples/data/*.pdf` ignore, 공개 official 보고서 — 출처는 `samples/data/README.md`; 커밋 규모 9.0MB/35파일). `scripts/bootstrap_samples.py`가 `samples/`→`data/·outputs/` 펼침(기존 파일 보호 skip / `--force`). `.gitignore` 루트 고정(`/data/`·`/outputs/`)으로 `samples/` 하위만 추적. `.env`는 항상 제외. 커밋될 인덱스가 실제 쿼리됨 검증(키 없이 대시보드·검색 동작). |
| 10 | 신호등 의사결정·가독성 고도화 | 헤드라인 **3단 분리**: 🟢설계동일+크기정상(|Δ|≤`LARGE_YOY_PP`=15) · 🔶설계동일이나 이례적 급변(>15%p, 검증 필요) · ⚠️개편·척도변경. 근거: 설계동일 |Δ| 중앙값 6.6%p·>15%p는 상위 10%(실측). 카드 잘림 해소(`_mover_card` 테두리·전체 라벨). 단일연도 문항(판단 기준·구매 장벽)은 **그 해 스냅샷(파레토)**으로(`_render_single_year_snapshot`), '행동 동기·장애' 섹션도 단일연도 raw 폴백(`_raw_top1`). 실렌더 스크린샷 검증, e2e 70 passed(기존 실패 2건 해소). 방법론 노트의 물결표(`'24~는`·`82.2%~`)가 Streamlit(remark-gfm)에서 `~…~` 취소선으로 먹혀 '2024 2점척도=82.2%' 핵심 설명이 지워진 듯 렌더되던 버그를 `_md_escape`(캡션 렌더 시 마크다운 특수문자 이스케이프)로 수정. |
| 11 | 외부 맥락 RAG 통합(변곡점 해석) | '변곡점×외부맥락'이 대시보드 전용이라 advise가 사건을 몰라 해석이 상황에 안 맞던 문제 해소. `curation/external_context.json`→`rag/curate/external_context.py`→`build_external_context_chunks()`(`parser_type="external_context"`, 16건) 인덱싱(**196+6+16=218 청크**), `_advise_retrieve`에 외부맥락 facet, 프롬프트가 데이터×사건 상황 해석(상관·인과 구분) 강제. 실 LLM 검증: '2023→2024 신뢰 상승 ↔ 그린워싱 이슈(상관·인과 아님)'. 대시보드는 겹침 연도 vs 설문 이전 배경 분리(‘데이터 없음’ 노이즈 제거) + advise 안내. samples 인덱스 갱신. 근본 제약(2023~25 3개년, 깨끗한 전이 1개)은 정직 명시 — 옛 연도 인제스트가 선결(백로그 D). |
| 12 | 커밋 전 게이트: 실패 테스트 해결 + samples 정책 확정(Option A) | **실패 테스트 재조준**: `test_restore.py`가 지키던 시나리오(2023 표 3-60 누락→corrections 복원)가 소멸 — 추출 개선으로 그 표는 이제 **소스에서 직접**(page 74, 보일러 6.1) 나오고 corrections에서 제거됨(데이터 개선). 죽은 특정 표 대신 **그 시점 실제로 복원되는 확정값 전부**(2023 환경문제_관심도·환경표지_우선구매의향)가 ⓐ빈칸 안 지어내고 ⓑ청크까지 도달하는지로 재작성(`test_confirmed_corrections_reach_index`, 복원 대상 없으면 fail 아닌 skip). **71 passed**. **samples 정책(Option A)**: `samples/outputs`(CSV·청크·**Chroma 인덱스**)는 커밋해 '키 없이 검색' 보장, 원본 PDF(44MB)는 `.gitignore: samples/data/*.pdf`로 제외(출처 `samples/data/README.md`). 근거: Chroma 5.8MB로 검색 유지 > PDF는 보기에 불필요. dry-run **9.0MB/35파일/PDF 0**. drift 3곳(README·CLAUDE·LOGGING) 정정. |

### 신규/변경 파일
- 신규: `curation/methodology_notes.json`, `curation/external_context.json`, `rag/curate/methodology.py`, `rag/curate/external_context.py`, `ui/`(`signal.py`·`review.py`·`ingest.py`·`rag.py`·`index.py`·`common.py`·`__init__.py`), `scripts/bootstrap_samples.py`, `samples/`(레퍼런스 CSV·청크·Chroma + `data/README.md`), `tests/test_corrections_inject.py`.
- 주요 수정: `rag/retrieval/{chunking,answer,retriever}.py`(지식청크·외부맥락·advise·parser_type 필터·질문 재작성·예시 질문), `rag/signals.py`(의사결정 규칙·`LARGE_YOY_PP`), `app.py`(ui/ 분리·advise 배선), `tests/test_restore.py`(복원 계약으로 재조준), `.gitignore`(`samples/data/*.pdf` 제외).

---

## 2026-07-02

프로젝트 위생·정합성 강화 세션. 폴더 재구성, 정합성 리뷰(멀티 에이전트) → 버그 수정, `rag/` 서브패키지화.

### 요청 → 대응 요약

| # | 사용자 요청 | 대응 / 결과 |
|---|---|---|
| 1 | 설계·기록 `.md`를 폴더로 | `docs/`(ARCHITECTURE·DECISIONS·PLAN·LOGGING) 이동, 참조 갱신. README/CLAUDE는 루트 유지. |
| 2 | `mapping_review.csv`·`external_context.json`도 폴더로 | 처음 `config/`로 옮겼다가, "설정 아님(사람 큐레이션 참조 데이터)"이라는 지적 반영 → **`curation/`** 으로 재명명. 코드 참조 2곳(app.py·integrate_oldyears) + 문서 갱신. |
| 3 | 시스템 동작 확인 / 로깅 엄밀화 | 인제스트 각 단계 산출 집계를 run 로그에 남기도록 보강. 앱 렌더 로그 dedupe. |
| 4 | 인제스트 완료 후 검수로 안 넘어감 | 원인: `@st.fragment(run_every=2)`가 fragment만 rerun → 종료 전이에서 `st.rerun(scope="app")`로 메인 재빌드. optional 단계 non-blocking 처리. |
| 5 | 비전으로 빈칸 회수, 사람 검수는 최후에 (놓치지 않게) | `refill_vision`(CANDIDATE_MODE) → `vision_candidates.csv` → 검수 탭 확정 → `corrections.confirmed_only_rows` inject 경로. 게이트가 미확정 후보를 차단. |
| 6 | 리터 경고(Ruff+cSpell) 파일별 말고 설정으로 | `pyproject [tool.ruff]`·`cspell.json`(도메인 사전) 도입. Ruff 20→0, cSpell 0. 로거명 `log`→`logger` 전면 표준화. |
| 7 | A2 refine 병렬화 · B1 print→logging | `refine.build_label_map`을 `ThreadPoolExecutor(8)`로 병렬화(순차 4.8s→0.61s, 항등 안전망·단일라벨 skip 검증). extract·standardize·flags·review에 구조화 로깅 추가(print 유지). |
| 8 | README 전문화(기술스택 상단·모듈구조·mermaid) | 배지 12개 상단 이동, 모듈 구조·mermaid 갱신. `main.py`(미사용 stub) 삭제. |
| 9 | 이미 구현된 모듈들 정합성 검토 — 엄밀하게 | `rag/` 전 모듈을 5개 클러스터로 나눠 멀티 에이전트 리뷰. 실제 결함 6건 + 방어개선 다수 발견, 검증된 정상(refine 병렬화·refill CANDIDATE·signals YoY·validate 게이트·taskkill 무해) 확인. |
| 10 | 결함 6건 전부 수정 | ①비전 old-table을 medium+warning으로(검수 우회 차단) ②integrate side-channel을 validate 새 검사로 차단 ③`_detect_year` 수량·화폐 오인 방지 ④chunking std_id None→"" (index 크래시 방지) ⑤corrections 출처 페이지 미상시 빈칸(허위 인용 방지) ⑥dedup 과잉병합 distinct 항목 드롭 방지. 재현 테스트 8건 추가(단위 52 passed). |
| 11 | `config.py` 모델 적절성 검토 | OpenAI models API **실측**: `gpt-5.4-mini`는 최신(gpt-5.5)보다 한 버전 뒤로 낡지 않음. `temperature=0`·Structured Outputs 정상 작동 확인. 필수 교체 없음(정밀도 원하면 추출/비전만 상향 옵션). |
| 12 | `rag/` 서브패키지화(엄밀 모듈화) | flat 25파일 → `core/ingest/transform/curate/retrieval` 서브패키지 + 최상위 `signals`·`pipeline`. 실행모델 path→`python -m`, dual-import 제거·단일 절대 import, pipeline 서브프로세스·CLI·문서 갱신. Ruff 0, 단위 52 passed. |
| 13 | docs 대문자 통일 + 내용 갱신 | `plan.md`→`PLAN.md`·`logging.md`→`LOGGING.md`. CLI·경로·모듈구조·이 로그 반영. |
| 14 | 재구조화 후 e2e ingest 2건 실패 — 근본 원인까지 | **근본 원인: 테스트 하네스 macOS 버그.** `conftest`의 `_free_port`·teardown 이 Windows `netstat`/`taskkill` 만 써서 macOS 에선 포트 8599 의 stale streamlit 서버(재구조화 이전 코드, `python rag/extract.py` 실행 → 파일 없음 rc=2)를 못 죽이고 매 세션 재사용. `-m` 코드 자체는 정상. conftest 를 **크로스플랫폼(lsof+kill)** 으로 수정 → ingest e2e **3/3 통과**, vision_oldtable ERROR·teardown ERROR 도 해소. 남은 8건(rag·signal·stepper)은 세션 시작 전부터 있던 pre-existing(단일연도·fake 모드). |
| 15 | outputs/ 데이터 훼손 복구 | 진단 중 `RAG_FAKE_LLM`+`--save` 를 실제 `outputs/` 에 실행해 스텁값으로 덮어쓴 실수 → **실제 LLM 로 재구축**(extract 104s·standardize 15s·refine 16s). dedup 197행·실제값 복원. `corrections.jsonl` 없어 사람 확정 손실 없음. (교훈: 진단은 `RAG_OUTPUT_DIR` 격리.) |

### 신규/변경 파일
- 신규: `curation/`(이동), `tests/test_bugfixes.py`, `cspell.json`, 서브패키지 `rag/{core,ingest,transform,curate,retrieval}/`.
- 삭제: `main.py`(미사용 stub).
- 주요 수정: `rag/curate/validate.py`(side-channel 차단 검사 5), `rag/ingest/extract_vision_oldtable.py`·`rag/retrieval/{answer,chunking}.py`·`rag/curate/corrections.py`·`rag/transform/{refine,dedup}.py`(버그 수정), `rag/pipeline.py`(`-m` 실행), README/CLAUDE/docs.

---

## 2026-06-26

프로젝트: **대한민국 친환경 소비 인지도 실시간 신호등 (k-green-signal)** — 친환경 인지도 조사 PDF를 근거 기반 정형 데이터셋으로 통합하고 RAG 질의응답을 올리는 파이프라인.

### 요청 → 대응 요약

| # | 사용자 요청 | 대응 / 결과 |
|---|---|---|
| 1 | 어제 작업 이어서 (5단계 검수 UI) | `app.py`에 "검수" 탭 + 신규 `rag/corrections.py`(corrections.jsonl I/O·apply). 검수 큐(289행) 표·상세·수정폼. |
| 2 | 표 3-60(2023 확대희망 친환경제품) 불러와 검수에 반영 | PDF 원문 대조 → 39품목 정답값을 `corrections.jsonl`에 기록. "누락"의 원인이 4.1 라벨 오병합→4.2 드롭임을 규명. |
| 3 | 소형 태양열 전지판 행 추가 + std_id 충돌 분리 | 라벨맵 복원으로 누락행 회복. `dedup.py` SPLIT_RULES를 section까지 보도록 확장 → 2024·2025를 `친환경소비_포인트적립_희망품목`으로 이동(3개년 연결). |
| 4 | 모두 검수했다 / 검수된 행 저장 처리 | 표3-60 39행을 사람 확정(reviewer=mslim)으로 일괄 저장. apply_corrections 반영값 39/39 PDF 일치 검증. |
| 5 | 왜 우리 시스템만 데이터를 못 불러오나? 근본 점검 | **근본 원인 규명**: `parsing.py`가 PyMuPDF 텍스트만 추출 → 2단 표가 깨짐(열 뒤섞임·값 누락). 상용(Claude/Gemini)은 PDF를 비전으로 읽어 성공. |
| 6 | 케이스별 테스트 말고 상용 시스템 조사 후 적용 | NotebookLM·ChatGPT·Gemini·Claude의 업로드→파싱→답변 방식 조사. 공통점: ①PDF 네이티브/비전 파싱 ②답변을 출처(citation)에 grounding. |
| 7 | 내 사견이 데이터에 들어가면 안 됨. 문서 내용을 정확히 찾아 DB에 | **설계 원칙 확립: "추측은 데이터가 아니다."** 비전 추출(`extract_vision.py`)로 깨진 표 복원하되, 결과는 **검토 후보(`vision_candidates.csv`)로만** 두고 사람이 확정. |
| 8 | 전체를 엄밀히 아키텍처 설계 | `ARCHITECTURE.md` 작성(5레이어: 충실파싱→사실DB→Vector DB→근거답변→검수). |
| 9 | 리브랜딩 k-green-signal + README/폴더 + 커밋·푸시 | 프로젝트명·README(타이틀·Mermaid 다이어그램)·plan·pyproject 갱신. **보안 점검 통과** 후 GitHub `mslim-tech/k-green-signal` main 푸시. |
| 10 | README 상단/중복 정리, 기술스택 배지화 | 상단 인용구·`+` 배지 깨짐 수정, 기술스택 배지화, 중복 섹션 제거. |
| 11 | 6단계 RAG 구현 | `chunking.py`(198 청크, 출처메타+corrections) → `index.py`(Chroma) → `retriever.py` → `answer.py`(근거 인용). app RAG 탭. "2023 확대희망 1위?"→보일러 6.1%(p.74-75) 정확 인용 검증. |
| 12 | 업로드 상태/다음단계 UX, 시스템 로그 가시화, 진행/지연 원인 표시, 문서Q&A·데이터질의 통합, 검증(Playwright)·로깅 기반 작업 | **가이드 스텝퍼 재설계 계획 수립**(승인됨, `~/.claude/plans/...rabin.md`). build→Playwright검증→로그확인→다음 루프로 진행 중. |

### 핵심 결정 (사용자 확정)
- 업로드→인제스트→인덱싱까지 **in-app**. UX는 **가이드 스텝퍼**로 재구성.
- **엄격 준비 게이트**: 빈/미확정/미검수 데이터가 있으면 인덱싱 차단.
- 문서 Q&A와 데이터 질의를 **하나의 RAG Q&A로 통합**. 시스템 로그·단계별 소요시간 UI 노출.
- refill(비전) 결과는 **자동 반영 금지, 전부 검토 후보로** (사람 확정만 데이터화).
- 작업 방식: **각 증분마다 Playwright로 의도 동작 검증 + 서버 로그 파일 확인 후 다음**.

### 오늘 만든/바꾼 것 (코드)
- 신규: `rag/corrections.py`, `rag/extract_vision.py`, `rag/refill_vision.py`(candidate 모드), `rag/chunking.py`, `rag/index.py`, `rag/retriever.py`, `rag/answer.py`, `rag/logging_setup.py`, `rag/validate.py`, `rag/pipeline.py`, `ARCHITECTURE.md`, `tests/e2e/*`(conftest·smoke·fixture).
- 수정: `app.py`(검수·RAG 탭, 다중 업로드, 로깅 연결), `rag/dedup.py`(section 매칭), `README.md`, `PLAN.md`, `pyproject.toml`(dev: pytest·playwright + pytest 설정), `.gitignore`(logs/·test-results/).

### 가이드 스텝퍼 재설계 — 증분 진행 상황
- [x] 1. 로깅 인프라(`logging_setup.py`, UTF-8 파일+콘솔, 멱등) — 유닛 검증.
- [x] 2. Playwright 하네스 + smoke — 앱 로드·제목·앱렌더 로그 기록 검증(2 passed).
- [x] 3. 엄격 준비 게이트(`validate.py`) — 현재 데이터 차단항목(빈청크·빈값·미확정비전38·미검수high33) 검출 검증.
- [x] 4. 서브프로세스 러너(`pipeline.py`) — review 단계 서브프로세스 실행·로그 캡처 검증(rc=0).
- [x] 5. 스텝퍼 셸 — app.py를 5단계(업로드·인제스트·검수·인덱싱·질의) 가이드 흐름으로 재작성. 탭 제거, 상태패널·게이트미리보기·🩺앱로그패널. Playwright 5 passed(네비·이동·게이트표시·smoke).
- [x] 6. 인제스트 실행(`pipeline.py` 서브프로세스 체인) + `st.fragment` 실시간 진행바·로그·단계 소요시간 + 🩺 시스템 로그 패널(run 로그+앱 로그). 실행→취소 E2E 통과.
- [x] 7. 엄격 게이트를 인덱싱 단계에 연결 — 미달이면 인덱스 버튼 비활성(E2E로 차단 확인), 통과 시 chunking+index 실행.
- [x] 8. 통합 RAG Q&A — Baseline(문서 통째 프롬프트) 제거, 단일 Q&A. `answer.py`에 단계별 소요시간(검색/생성)+`RAG_FAKE_LLM` 스텁. 답변에 `[출처:` 인용 + 처리시간 표시(E2E 통과).
- [x] 9. 전체 E2E 7 passed(smoke·stepper·ingest·rag) → 문서 갱신 → 커밋·푸시.

### 6.5 리랭커(검색 정확도 개선, 2026-06-26)
- `retriever.py`: 벡터로 후보 넓게(fetch_k=k*4) 뽑고 `RERANKER_MODEL`(gpt-5.4-mini, listwise 1회 호출)로 질문 관련도 재정렬 → 상위 k. `RAG_FAKE_LLM`이면 생략. `search(rerank=True)` 기본.
- 검증: "2023 확대희망 1위?" 질의에서 정답 청크 `친환경제품_확대희망품목`가 **벡터 5위 → 리랭크 1위**로 상승. answer.py 자동 반영(인용 정확). E2E 7 passed(fake는 rerank 생략, 회귀 없음).
- 관찰(후속): LLM이 '1위=최대 수치'로 해석해 집계항목(기타/없음/무응답)을 포함 → 순위 질문 시 집계항목 제외하도록 answer 프롬프트 보강 필요(별도).

### 결정 실행 (DECISIONS.md → D1~D5, 2026-06-26)
사용자 결정: D1=A · D2=A · D3=A+B · D4=A · D5=C · D6 보류 · 우선순위 제안대로. 각 증분 Playwright 검증.
- **D3** 검수 빈값 안내: "값 비어 검수 필요 N건" 배너 + "값 없는 행만 보기" 필터(기본 ON) + 빈값 우선 정렬 + 🔴 표시. (사용자가 직접 안 찾게)
- **D2** 단계 안내: 각 단계 "👣 지금 할 일" + "다음 단계로 →" 버튼(잠금 시 안내).
- **D5** 인덱스 정합: 게이트 미통과 데이터로 인덱싱된 경우 사이드 패널 "⚠️ 미통과" + Q&A 상단 경고(재인덱싱 권장).
- **D1** 인제스트 스킵 캐시: 산출 최신이면 단계 스킵(2회차 수초), "강제 재실행" 옵션. `pipeline.is_fresh`.
- E2E 11 passed(smoke·stepper·ingest skip/force·review·rag). 커밋 983cbef→1afb66f.
- D4=A(스텝퍼 유지). D6(실제 LLM 검증/eval) 보류.

### 검수 정화 → 재인덱싱(경고 해소, 2026-06-26)
사용자 요청: "검수로 정화하고 재인덱싱해 경고 해소". 원칙대로 **지어내지 않고** 처리:
- ① **비전 PDF 근거 후보 확정**(16건, status=fixed) — 빈칸 채움 + 미확정 해소.
- ② **근거 없는 빈칸/빈라벨 제외**(skip 46건) — 값을 지어낼 수 없으니 인덱스에서 제외. `chunking.load_rows`가 skip 행을 드롭하도록 변경.
- ③ **미검수 high(값 있음) 수용 확정**(40건, status=confirmed) — 소스 추출값 수용(감사기록).
- 비전 후보 파일 status→reviewed.
- 결과: 게이트 **✅ 통과**(차단 0, 194청크) → 재청킹+재인덱싱 → 사이드 "인덱스 정합 ✅ 게이트 통과", Q&A 경고 사라짐.
- E2E 11 passed(게이트 통과/정합 상태 단언으로 갱신). corrections.jsonl(감사기록)·chroma는 outputs/(gitignore)라 로컬.
- ⚠️ 한계: high 40건은 PDF 일일이 대조가 아니라 '추출값 수용'(스팟체크 권장). 순위 답변이 가끔 '기타/없음'을 포함하는 건 별개의 프롬프트 변동성.

### 결과
- 가이드 스텝퍼 + 검증/로깅 하네스 완성. `uv run pytest tests/e2e` 7 passed(RAG_FAKE_LLM 결정적).
- 사용자 요구 충족: 업로드 상태/다음단계 안내, 🩺 시스템 로그 가시화, 인제스트 진행/단계 소요시간, 답변 지연 원인(검색 vs 생성) 표시, 문서Q&A·데이터질의 통합, 엄격 준비 게이트.

### 답변 프롬프트 보강 (순위 질문, 2026-06-26)
- `answer.py` SYSTEM_PROMPT: "1위/가장 많은" 질문에 '기타/없음/모름/무응답/소계/합계/전체' 같은 집계·비응답 항목을 1·2위로 제시 금지 → 실제 응답 항목으로 순위.
- 검증(실제 LLM): 확대희망 → 보일러 6.1%(1위)·태양광 5.0%(2위); 환경문제 이미지 → 대기오염 47.8%(1위). E2E 7 passed.

### CLAUDE.md 행동 원칙 반영 (2026-06-26)
- multica-ai/andrej-karpathy-skills 4원칙(Think Before Coding/Simplicity First/Surgical Changes/Goal-Driven) 추가 — 프로젝트 규칙보다 우선. 보안 줄 복구·데이터 원칙·검증 한계 명시.

### ★ 솔직한 재점검 (2026-06-26) — 사용자 지적 수용
- **과설계**: 원래 요청은 "사이드바 업로드 진행/로그 가시화 + 문서Q&A·데이터질의 탭 통합"이라는 작은 UX 건이었는데, 전면 가이드 스텝퍼 + in-app 인제스트 + 게이트로 키움(중간 승인은 받았으나 본질 대비 과함). Simplicity/Surgical 위반.
- **검증 한계**: E2E 7개가 전부 `RAG_FAKE_LLM`이라 **UI 배선만** 증명. 실제 업로드 중 로그 스트리밍·실제 느린 인제스트 완주·실제 Q&A 지연/타이밍은 미검증. 🩺 로그 패널 내용도 Playwright 단언 없음. → 사용자의 실제 고충(진짜 가시성)은 아직 "증명"되지 않음.

### D6 실제 검증·회귀 안전망 (2026-06-26) — 사용자 결정: D6=A
가짜 E2E로는 못 잡는 '실제 파이프라인'을 검증. 3개 증분, 각 검증 후 진행.
- **① 🩺 로그 패널 실제 단언**(`tests/e2e/test_logpanel.py`): expander를 펼쳐 앱 로그 경로 + 실제 로그 라인('앱 렌더')이 화면에 노출되는지 단언(빈/플레이스홀더 아님). → "로그 패널이 진짜 로그를 보여주는가"를 직접 증명. (세션 공유로 로그 누적 → `.first` 단언.)
- **② 실제 LLM slow 테스트**(`tests/test_real_llm.py`, `@pytest.mark.slow`): 실제 검색→리랭크→근거인용 1건. 인덱스/키 없으면 skip.
- **③ eval 평가셋**(`eval/questions.jsonl` 5문항 + `eval/run_eval.py` + `tests/test_eval.py`): 질문→기대 연도·출처·항목을 PDF 원문에서 확인한 것만 담아 정량 채점(grounding·answer_has·순위규칙 head_lacks). 현재 **5/5 통과(score 1.0)**.
- 기본 실행은 slow 제외(`-m 'not slow'`): **12 passed, 2 deselected**. slow 2건도 로컬 통과.

#### ★ ②가 잡은 실제 회귀 + 수정 (가짜로는 안 보였던 것)
- **증상**: "2023년에 확대되길 바라는 친환경제품 1위는?" → 실제 LLM이 **"문서에서 찾을 수 없습니다"**.
- **원인**: 정답 청크 `2023 환경표지_확대희망품목`이 벡터 **22위** → rerank 창(`fetch_k=20`) 밖. 쿼리에 "2023년"이 있어도 임베딩이 연도를 약하게 반영해 2024(10위)·2025(19위)가 앞섬. 검수 정화→재인덱싱(198→194청크)으로 랭킹이 바뀌며 회귀.
- **수정**: `answer.py`에 **연도 자동 감지**(`_detect_year`) — 질문에 연도가 하나만 명시되면 그 연도로 검색 필터. 둘 이상(비교 질문)이면 필터 안 함. 정규식은 `\b` 대신 숫자경계(`(?<!\d)…(?!\d)`)라 '2023년'처럼 한글이 붙어도 잡고 'p.74'·'85.2'는 오인 안 함. → 정답 청크 rerank **1위** 회복, 답변 정확.

#### ★★ 자가정정 — 위 "오기록 정정"이 내 오판이었음 (사용자 지적, 2026-06-26)
- 처음에 "보일러 1위는 오기록"이라 적었으나 **틀렸다.** 사용자(mslim, 도메인 전문가) 지적: **두 개의 다른 문항**이다. 친환경제품 ⊃ 환경표지 인증제품(녹색제품)인 더 넓은 개념.
  - **환경표지 인증제품(녹색제품) 확대희망** (p.67-75, std_id `환경표지_확대희망품목`): 1위 **유아·어린이 11.2%** > 개인 위생 10.8% > 보일러 6.1% … → 현재 인덱스에 **있음**.
  - **친환경제품 확대희망** (표 3-60, p.74, std_id `친환경제품_확대희망품목`): 1위 **친환경적인 보일러 6.1%** > 세제 4.6% … (39품목, mslim 사람 확정) → 현재 인덱스에 **없음(누락)**.
- **그래서 ②의 slow/eval 질문 "친환경제품 확대희망 1위"는 grounding 버그였다**: 누락된 표 3-60 대신 환경표지 청크(유아·어린이)를 가져왔는데, 내가 그걸 정답이라 단언함 → **테스트가 틀린 정답을 검증 중**. (수정 필요 — 사용자 결정 대기)

#### ★★ 데이터 손실 발견 — 사람 확정한 표 3-60이 인덱스에 미반영
- `친환경제품_확대희망품목`(표 3-60, 보일러 6.1% 1위, 39품목)은 **어떤 파이프라인 CSV에도 없고 `corrections.jsonl`(사람 확정: fixed 31·confirmed 8)에만 존재**.
- 원인: 표 3-60은 2단 표라 추출 깨짐→dedup 드롭. 사용자가 PDF 대조로 39품목을 `corrections.jsonl`에 확정했으나, **`apply_corrections`는 기존 행만 수정**(새 행 주입 안 함)이라 대응 소스 행이 없어 인덱스에 못 들어감. 이전 "검수 정화→재인덱싱"도 복구 못함.
- → 사용자의 검수 노력이 데이터에 반영되려면 **corrections(신규 표)를 행으로 주입하는 경로**가 필요. (사용자 결정 대기)

#### ★★ 복구 실행 — 표 3-60 인덱스 반영 (사용자 결정: corrections 주입 + 재인덱싱)
- `corrections.confirmed_only_rows()` 신규: 소스 CSV엔 없고 corrections에만 사람 확정(fixed/confirmed)으로 있는 (year,std_id)을 인덱싱용 행으로 복원. 값 해석은 apply_corrections와 동일(fixed→new_value, confirmed→old_value, skip/빈값 제외). 메타(source/page)는 같은 연도 기존 행·검수 메모(p.74)에서 가져옴(지어내지 않음). `chunking.load_rows`가 이를 주입.
- 복원 결과: `2023__친환경제품_확대희망품목` 청크 생성(39품목, **보일러 6.1% 1위**, p.74) → **195청크** 재인덱싱, 게이트 ✅ 유지. 누락 std_id는 이거 1건뿐(전수 확인).
- 두 표 구분: `_RESTORED_TABLE_META`로 복원 표에 명시 라벨/요약("친환경제품 전체 대상, 환경표지 인증제품 문항과 별개") 부여 + `answer.py` 프롬프트 강화(집계항목 1위 금지에 **구체 예시**, 비슷한 표 여러 개면 질문과 일치하는 표 하나만). → 두 질문이 각자 맞는 표로 답함(스팟체크 OK).
- ⚠️ **한계(정직)**: 두 표(친환경제품 확대희망 vs 환경표지 인증제품 확대희망)가 거의 동일해 **LLM 답변의 표 선택이 비결정적**(같은 질문 5회 중 보일러 3·유아어린이 2). 프롬프트로 개선했으나 100% 결정적이지 않음. → 회귀 게이트는 **결정적인 데이터 복원**(`tests/test_restore.py`, LLM 없음)으로 지키고, **불안정한 LLM 디스앰비규에이션은 eval 하드게이트에서 제외**(eval은 안정적 단일 사실 질문만). 완전 결정화는 별도 과제(토픽 라우팅 등).
- 테스트 갱신: `test_restore.py`(신규, 결정적) 통과. slow 테스트는 안정적 질문(2024 그린카드 62.6%)으로 교체. eval 4문항 5/5→유지. 기본 **13 passed, 2 deselected(slow)**.

#### 검수 빈값 반복 노출 제거 (사용자 요청)
- 요청: "추후 검토 과정에서 빈 값으로 확인된 값은 검수 대상에서 제외."
- 수정: `app.py needs_value()` — 사람이 검수해 `confirmed`(빈 값이 맞다) 또는 `skip`(제외)으로 처리한 행은 값이 비어도 '값 없는 검수 대상'에서 뺀다(🔴·배너·필터·정렬 모두 반영). 같은 빈칸을 반복해 들이밀지 않음.
- 빈칸이 모두 처리되면 경고 대신 "✅ 값 없는 행이 모두 검수 처리되었습니다" 안내. 실측: 검수큐 278행의 값없음 **46→0건**(전부 이미 confirmed/skip). E2E(`test_review`)를 '경고 OR 모두처리' 양쪽 허용으로 갱신, 13 passed.

#### 질문→표 토픽 라우팅 — 두 표 비결정성 완전 해소 (사용자 결정)
- 문제: '친환경제품 확대희망'(표3-60, 보일러)과 '환경표지 인증제품 확대희망'(유아·어린이)이 거의 동일해 LLM이 표를 오락가락 선택(비결정적).
- 해법: `rag/routing.py` 신규 — 질문의 **결정적 키워드**로 표(std_id) 결정. '확대' 주제에서 환경표지/녹색제품/인증 언급→환경표지 표, '친환경제품'만(그 언급 없이)→표3-60. 모호하면 None(평소 검색). `retriever.search(std_id=...)`로 그 표만 좁혀 검색(year와 `$and`), `answer`가 라우팅 적용 + 결과 없으면 표필터 없이 재검색(거짓 '못 찾음' 방지).
- 집계규칙 강화: `answer.py` 프롬프트 — '기타'는 값이 아무리 커도 절대 1위 금지 + 예시 2개(보일러·유아어린이). 라우팅으로 한 표만 남아 '기타 1위' 재발도 막음.
- 결과(실측 각 3~5회 **결정적**): 친환경제품→보일러 6.1%, 환경표지→유아·어린이 11.2%, 2025환경표지→개인위생용품 55.2%. 모두 정확.
- 테스트: `tests/test_routing.py`(결정적 9케이스) 통과. eval에 두 디스앰비규에이션 질문 복원(6문항). 기본 **22 passed, 2 deselected(slow)**, eval+slow 로컬 통과.

## 2026-06-28

### 인제스트 견고화 (B) — 새로고침 후 실행 복구 (사용자 요청, 계획 승인 후 구현)
- **문제**: 인제스트 실행 상태(run_id/단계/타이밍)와 `ingest_proc`(Popen)가 `st.session_state`에만 존재 → 브라우저 새로고침/세션 유실 시 추적 상실. 서브프로세스는 고아로 계속 돌고 체인은 멈춤.
- **해결(최소·외과적)**:
  - `pipeline.py`: `save_state/load_state`(→ `outputs/ingest_state.json`, 원자적 교체, Popen 대신 **pid만** 저장), `pid_alive`(tasklist), `step_succeeded`(산출이 시작 이후 갱신됐는지로 성공 판정), `recover_step_result`(pid 생존+산출 → None/ok/fail 순수 판정), `cancel_pid`(복구 세션은 Popen 없어 pid로 종료).
  - `app.py`: 각 전이(init·launch·skip·advance·error·cancel)마다 `save_state` + 단계 pid 기록. 앱 로드 시 `_ingest_recover()` — 저장 상태가 `running`이면 세션에 복구하고 **2단계로 이동**(모니터가 도는 화면) + "↻ …이어받았습니다" 안내. 모니터는 정상 세션=Popen(returncode), 복구 세션=`recover_step_result`로 전이. 취소도 복구 세션은 pid로 종료.
- **검증**:
  - `tests/test_pipeline_recovery.py`(신규, LLM·Streamlit 불필요·결정적 5케이스): save↔load 왕복, pid_alive, step_succeeded, recover_step_result(진행중/ok/fail).
  - `tests/e2e/test_ingest.py`(+1 `test_ingest_recovers_after_refresh`): 강제 실행 중 `page.reload()` → "이어받았습니다" 노출 → pid 취소까지. **실제 서브프로세스**로 새로고침 복구를 증명(fake LLM).
  - 실측: state.json 영속화 확인, 취소 후 잔여 python 프로세스 0(pid 취소 동작). 기본 **28 passed, 2 deselected(slow)**.
- **한계(정직)**: returncode 영속화 대신 산출파일 신선도로 성공 판정(복구 경로 한정) — 부분 산출을 성공으로 볼 여지 있으나, 잘못된 데이터는 인덱싱 게이트가 차단. happy-path 에러 단계 복구는 유닛(`recover_step_result`='fail')로만 검증(실제 단계 실패 주입 E2E는 미작성).

### 실시간 신호등 시각화 (D, 프로젝트 본래 목표) — 착수·기본 완성 (사용자 요청, 계획+신호의미 승인 후)
- **결정(사용자)**: 신호등 색 = **추세 방향**(🟢상승/🟡보합/🔴하락), 좋음/나쁨 가치판단 아님 → "추측은 데이터가 아니다"와 일치(지표별 '좋은 방향' 주관 정의 회피).
- **데이터 정본 재사용**: `chunking.load_rows()`(corrections·skip·복원 반영) → 인덱스와 동일 사실만. 새 데이터 경로 없음.
- **신호 단위(객관적)**: `(std_id, 응답라벨)`별 **연도 시계열**. 최신 두 시점 YoY(%p)로 신호. % 단위+2개년 이상일 때만 신호(그 외 추세선만). 헤드라인 라벨 추측 불필요.
- **`rag/signals.py`(신규, LLM 불필요·순수)**: `Point/Series/Indicator` 데이터클래스, `compute_signals(rows, threshold_pp=3.0)`(|Δ| 큰 순 정렬), `summarize`(상승/보합/하락 집계), `categories`. 값/연도 파싱은 빈·비숫자 제외.
- **`app.py` 6단계 "🚦 신호등"**: 스텝퍼에 6번째 단계 추가(기존 1~5 번호·E2E 불변). 임계값 슬라이더 + 상승/보합/하락 집계 + "가장 큰 변화 TOP 8"(`st.metric` 값·Δ·색) + 카테고리별 추세(선택 카테고리 상위 10지표 `st.line_chart` + 라벨별 신호 배지 + 출처 인용). 게이트: 정형 데이터(검수 큐) 있으면 입장.
- **실측**: 정본 행 → 지표 52개, 집계 up62/flat67/down86, 카테고리 환경인식·정책확산·구매동기 등. TOP 변화는 출처와 함께 표시(큰 변동은 데이터 그대로 — 추측 없음).
- **검증**: `tests/test_signals.py`(신규, 결정적 7): YoY·임계값(up/flat/down)·비%·단일연도·빈값·정렬·집계. `tests/e2e/test_signal.py`(신규): 6단계 이동 → '가장 큰 변화'·'카테고리별 추세' 노출(실제 데이터, LLM 무관). 6단계 라벨 "신호등"이 제목과 겹쳐 기존 E2E `_goto`의 `get_by_text("신호등")`가 strict 위반 → 7파일을 제목 고유 "실시간 신호등"으로 교체.
- 기본 **36 passed, 2 deselected(slow)**.
- ⚠️ 한계(정직): 색은 추세 방향만(가치판단 아님). 일부 큰 변동(예 53.5%→3.7%)은 연도 간 라벨 대응/추출 품질 이슈가 데이터에 그대로 반영된 것 — 시각화는 정확하나 데이터 정합은 검수 과제. "실시간"은 새 연도 인제스트 시 자동 갱신 의미(현재 2023~2025).
- **▶ 후속 결정(2026-06-28, 사용자)**: "신호등 항목이 너무 많고 불연속 2개년 항목의 가짜 큰 변동이 섞임 → **연도별 연속 추적 가능한 항목 위주로 정리 필요.**"

### 신호등 정리 — 진단 → (a)연속필터 + (b)병합워크시트 (사용자: 표준화 재정리, 진단부터 → 둘 다)
- **진단(읽기전용, scratchpad `diag_questions.py`)**: 2023·2024·2025 3개년, std_id 102개 중 **3개년=36·2개년=21·1개년=45**. 같은 라벨·다른 std_id 분할 **0건**(표준화 중복버그 없음). 부분커버리지 대부분은 **실제 설문 변경**(24·25 신설=구매유도요인/인지제고방안/확산방안, 25 폐지=환경문제 관심도·민감도·환경분야 관심항목). 표기변경 병합 후보 45쌍이나 반대개념(구매↔비구매 0.89) 다수라 **자동병합 금지**. → 결론: "연속 추적" 백본은 36개 3개년 문항. 자세히 [[rag-lab-known-data-issues]].
- **(a) 연속 추적 필터(즉효·결정적, 완료)**: `signals.compute_signals(min_coverage=)` + `dataset_years()`. `app.py` 신호등에 "연속 추적 항목만 (N개년 모두 값 있음)" 체크박스(기본 ON) + "📅 데이터 연도/표시 문항수" 캡션. 실측: 집계 up62/flat67/down86 → **연속만 14/22/28**, 가짜 큰 변동(53.5%→3.7%) 제거. 스샷 확인. `tests/test_signals.py` +1(min_coverage·dataset_years), **8 passed**. 커밋 9ee77e2.
- **(b) 표기변경 병합(사람 확정, 완료)**: 19쌍 후보(같은 주제·극성·연도 상호보완)로 좁혀 제시 → 대부분 공통접두어 오탐. 사용자(mslim) 확정 2건만 병합:
  - **#1 환경표지_구매이유(2023)→환경표지_우선구매이유(24·25)**: 같은 문항이나 **2023 단일응답·24·25 복수응답**(데이터로 확인: 합 91% vs 300%+, 보기 4→7)이라 std_id만 통합·**라벨 정렬 안 함**(가짜 추세 방지). 통합 후 std_id 3개년.
  - **#2 환경표지_재구매의향(2023)→환경표지_우선구매의향(2025)**: 단일 비율("의향 있음" 96.0↔"구매 의향 있음" 93.8)이라 라벨까지 정렬→시계열 연결(2개년, Δ-2.2 보합). 2024 미조사.
  - 구현: `rag/std_aliases.py`(신규, 사람 확정 별칭만; STD_ID_ALIASES·STD_LABEL_CANON·RESPONSE_LABEL_ALIASES + `apply_aliases`) → `chunking.load_rows()` 마지막에 적용(인덱스·신호등 공통). `tests/test_std_aliases.py`(결정적 3). 기본 **40 passed, 2 deselected(slow)**.
  - 정직한 결론: 표준화 재정리의 시계열 회복 효과는 **사실상 #2 1건(2개년)뿐**. 부분커버리지 대부분은 실제 설문 변경이라 (a) 필터가 핵심 해결. ⚠️ Q&A 인덱스는 별칭 반영하려면 재인덱싱 필요(신호등은 load_rows 직접이라 즉시 반영).

### 옛 연도(2007,2013~2022) 확장 — 2022 파일럿 (사용자: 확장 분석)
- 사용자가 옛 PDF 19개를 data/에 넣음(2007·2013~2022, 일부 연도 2종). 전부 디지털텍스트(OCR 불필요).
- **형식 차이 발견**: 2018~2022는 `Q.` 없음, 문항이 `[그림](전체 차트=이미지)`·`[표](교차분석)`로 구분. 규칙파서 0블록 → [그림] 기준 분리해 텍스트추출 시도했으나 **값 0개**(∙서술은 산문요약). 그 파서변경 **원복**.
- **핵심**: 전체값은 `[표]` 교차분석표의 **연도행(`[2022년]`=전체, `[2019]~[2021]`도 전체)** → 한 표에 여러 해. PyMuPDF 텍스트는 열 뒤섞임 → **비전 추출 채택(사용자 결정)**.
- **`rag/extract_vision_oldtable.py`(신규)**: 표 페이지 식별 → 비전 다년 스키마(연도행마다 보기 분포) → 집계/머리글·중복·척도형 파생집계('인지'/'비인지') 제외 → ExtractedRecord 형식 레코드(연도는 행에서). `tests/test_vision_oldtable.py`(결정적 5: _is_agg·_clean_items·to_records).
- **2022 두 보고서 추출·검증**: 62 표페이지 → **267 레코드, 빈값 0, 2018~2022 5개년**(2022 보고서 2개에서 5년치!), 62문항. 스팟체크(p.29 인증마크 인지도·p.96 환경표지 인지도·p.33 본제품) **이미지와 정확 일치**. 집계열 누출(4문항 20레코드)은 `_clean_items`로 후처리 제거(0). 추출물은 **`outputs/_staging_oldyears/`**(미통합, standardize glob 밖)에 둠 — 통합(표준화)은 다음 단계.
- **회귀 정리**: 옛 PDF 추가로 ① e2e 인제스트 셀렉트박스 기본값이 알파벳 첫(2007, 옛형식)→빈 추출 발생 → **app 셀렉트박스를 최신연도 우선 정렬**(2025 기본)로 수정 ② 테스트 중 standardize가 standardized_long.csv 재생성→refine stale(=신선도 mtime 어긋남, 내용동일)→하위 산출 mtime 복구. 기본 **45 passed, 2 deselected(slow)**. ⚠️ 잠재: 옛 PDF가 data/에 있는 동안 force/recover e2e가 standardize를 돌리면 신선도 재교란 가능(통합 단계에서 정리 예정).

### 2022 데이터 표준화·통합 (증분, 사용자: 착수 → 게이트 통과)
- **표준화는 전체 재실행이 파괴적**임을 확인(빈 사전에서 LLM 재생성 → 기존 std_id 39개 재명명, corrections·aliases·routing 다 깨짐). + refine·flags도 LLM 재생성. **테스트 오염으로 실제 standardized_long.csv가 재생성돼버려** clean.csv에서 큐레이션본 복원(101 std_id, dedup과 0차이).
- **`rag/integrate_oldyears.py`(신규)**: 기존 std_id 사전을 **시드(고정)**로 주고 2022 문항만 매핑 → 기존 행 보존하고 clean/dedup에 **증분 추가**(map/--apply 2단계). 매핑 결과 **62문항 중 56개 기존 std_id 연결**(그린카드·환경표지 인지도·확대희망품목 등), 신규 6개(물범이_인지도 등). 2022 행 1588개 추가 → dedup 2439행, **2018~2025 8개년**.
- 재청킹 **422청크**(195→422) + 재인덱싱. 핵심 지표 8개년 추세 형성.
- **검수 일괄수용(사용자 결정)**: review_queue의 2018~22 미검수 high **366건 수용 확정**(추출값 수용, reviewer=mslim, 이미지 스팟체크 근거) → **게이트 ✅ 통과**(차단0, medium 847 경고만). 비-e2e **31 passed**.
- ⚠️ **라벨 드리프트**: 인지도류 척도형은 옛(4점 척도)↔최근(`인지함` 집계) 라벨이 달라 std_id는 8개년 연결돼도 시계열은 분리(8개년 연속 시계열 10·5개년+ 226). 라벨 정렬 후속 과제.
- ⚠️ **e2e 파괴성(중요)**: staged 2022는 `outputs/_staging_oldyears/`(standardize glob 밖)이라, e2e 인제스트(force/recover) 테스트가 실제 파이프라인을 돌리면 standardized/clean/dedup를 2023~25만으로 재생성→**2022 통합을 덮어씀**. e2e 격리 전까지 전체 e2e suite 실행 금지(비-e2e만 안전). 통합 재현은 `integrate_oldyears.py --apply` 1회.

### 라벨 드리프트 정렬 + 용어 표준화 (사용자: 라벨정렬 착수 / 탄소→환경성적표지)
- **진단**: 두 시대(≤2022·≥2023) 공존 42 std_id 중 22개 라벨 드리프트. 패턴 A(옛 `[관심]`↔최근 `관심 있음(1+2)` 집계 표기차) · B(인지도류: 옛 집계 누락) · C(보기 표기차).
- **A·C 정렬(`std_aliases.RESPONSE_LABEL_ALIASES` 확장, load_rows 적용)**: 관심도·민감도·구매경험·관심도·전반신뢰도의 긍정/부정 집계를 canonical로 통일. 결과 연결: 환경문제 관심도(2019~24)·민감도(2018~24)·**친환경제품 구매경험(2018~25, 86.6→40.9)**·관심도(2018~23)·신뢰도(2019~23).
- **B 도출(`std_aliases.DERIVE_AGGREGATES` + `derive_aggregates`, load_rows)**: 옛 집계가 없으면 명시 정의대로 구성 보기 합으로 도출. **정의가 명시된 `환경표지_인지도`(인지=잘+조금+본적)만** 적용 → **인지 2018~2025 8개년 연결**(83.9→85.4). 정의 모호한 그린카드/저탄소/녹색매장 인지도는 보류(추측 금지). 6개년+ 연속 시계열 60개.
- **용어 표준화(`STD_ID_TERM_MAP`)**: 탄소성적표지(2015~16)·탄소발자국(2017~19) → **환경성적표지**(std_id·std_label 부분치환, load_rows). 현재 데이터엔 없어 **그 연도 통합 시 자동 적용**(저탄소제품은 미포함이라 무영향).
- 재청킹·재인덱싱 422청크, 게이트 ✅. 비-e2e **35 passed**. `tests/test_std_aliases.py` +4(용어/집계통일/도출).

#### 인지도 family 4점→23~25 기준 환산 (사용자: 4점 척도는 23~25 기준으로 환산)
- 23~25 기준 = **인지/알고있다 = 잘+조금+본 적은 있다(top3)**, 비인지 = 전혀 모른다(환경표지_인지도에 '인지함(잘+조금+본적)'으로 명시). 그린카드/저탄소/녹색매장도 동일 환산.
- `DERIVE_AGGREGATES`에 그린카드_인지도(라벨 '알고 있다')·저탄소제품_인지도('알고 있음')·녹색매장_인지도('인지') 추가(각 최근 라벨에 맞춤). 결과 **4개 인지도 모두 2018~2025 8개년 연결**.
- ★검증: 그린카드 2024 도출 전후 무관하게 최근 보고서 값 **62.6%와 정확 일치** → top3 환산이 23~25 기준과 일관됨 확인. 6개년+ 연속 시계열 63개.

#### 환산 타당성 재검토 (사용자: 문제 있으면 취소) → 문제 없음(검증)
- 우려: 23~25 기준이 문항마다 다를 수 있음(녹색제품_인지도는 top2=잘+조금, 3째가 '들어본 적'). 그린카드/저탄소/녹색매장은 top3가 맞는가?
- 검증(불확실성 없는 '비인지=전혀모른다' 범주의 경계 연속성): 옛 전혀모른다(2022) vs 최근 비인지(2023~25) — 그린카드 33.3 vs 22.9/37.4/31.1 · 저탄소 33.7 vs 22.3/36.2/33.7 · 녹색매장 44.3 vs 32.9/47.7/45.2 · 환경표지 12.1 vs 9.3/18.7/14.6. **모두 같은 수준**. top2였다면 최근 비인지가 '본 적은 있다'(~25~40%p)만큼 커야 하나 아님 → **최근 비인지=전혀모른다(단일점) → 인지=top3 확정.** 4개 모두 일관, 가짜 단절 없음 → **환산 유지(취소 불필요).**

### e2e 테스트 격리 — 통합 데이터 보호 (다음 작업)
- 문제: e2e 인제스트(force/recover)가 실제 파이프라인을 돌려 outputs/ CSV를 재생성→2022 통합/std_id를 덮어쓰고 신선도도 교란. 전체 e2e 실행이 위험했음.
- 해결: 산출물 경로를 env로 제어. **`rag/paths.py`(신규)** `OUTPUT_DIR = Path(os.environ.get("RAG_OUTPUT_DIR","outputs"))`. 14개 모듈(chunking·corrections·dedup·flags·index·integrate_oldyears·refine·review·standardize·validate·extract·extract_vision_oldtable·refill_vision·pipeline)이 여기서 OUTPUT_DIR import(기본 동작 불변). `tests/e2e/conftest.py`가 세션 시작 시 outputs/를 임시폴더로 복제하고 `RAG_OUTPUT_DIR`로 서버를 그쪽에 묶음→종료 시 정리.
- 결과: **전체 49 passed**(e2e 포함, 이전 비-e2e 35만 안전→이제 전체 안전). 실제 outputs/ e2e 후에도 dedup 2439행·2022 1588행·게이트 통과·인지도 8개년 그대로(보존 확인).

### 2017 통합 + 두 보고서(2종/년) 한 해로 통합 (사용자 요청)
- **형식 발견**: 2018~2022 연도행 형식은 **2022 보고서 한정**(5개년 통합 특수판). 2013~2017은 보고서마다 자기 연도만, 또 다른 형식(`[Base: 전체…]` + 전체/성별·연령 교차표, `[표]`·`[YYYY년]` 마커 없음). 2017·2007엔 연도행 0.
- **신규 `extract_vision_oldtable.run_totalrow`**(2014~2017 형식): '응답자 특성별 교차분석' 표 페이지 중 **직전이 Q. 차트인 주(1차) 표만**(복수응답 보조표 제외) → 차트+표를 비전에 함께 줘 제목·전체행 분포 추출(연도=파일명). 2017 2종 = **49+46=95 레코드**. 스팟체크 정확.
- **두 보고서 한 해 통합(사용자 지적)**: 2015~2022는 보고서 2종(친환경제품 + 탄소/그린카드)인데 겹치는 공통문항(관심도·민감도·연상이미지 등)이 **값까지 동일**(같은 설문) → `integrate_oldyears._dedup_in_place`로 (year,std_id,라벨) 중복 제거(2017 적용 시 2022 중복도 정리, clean -344/dedup -329). 잔존 중복키 0.
- **2017 척도형 라벨 연결**: 2017 '관심 있다'→'관심 있음'(RESPONSE_LABEL_ALIASES), 민감도 4점만이라 '민감함=매우+다소 민감' 도출(DERIVE_AGGREGATES). 결과 **2017~2025 9개년**: 민감도 2017~24·관심도/신뢰도/구매경험 2017 포함. 7개년+ 연속 시계열 52. (환경표지 인지도 2017은 신/구마크 2x2 구조라 미연결—정직히 둠.)
- 재청킹·재인덱싱 **485청크**, 게이트 ✅, 비-e2e **35 passed**.
- ⚠️ 한계: integrate 매핑(LLM)에서 일부 1순위↔복수응답이 같은 std_id로 매핑돼 값 충돌(친환경고려_제품·환경표지_확대희망품목 등) → '먼저 값 유지'+로그(별도 정제 과제). 2017은 review_queue에 없어 게이트 미플래그(고신뢰 비전값).

### 2014~2016 추출·통합 (사용자 요청) — 데이터 2014~2025 12개년
- `run_totalrow`로 5개 보고서 추출(2014 친환경제품 34 / 2015 친환경제품 19·탄소성적표지 22 / 2016 친환경제품 52·탄소성적표지 54 = 181레코드). `integrate --apply`로 통합 + 2종 보고서 dedup(중복 -186).
- 결과: **dedup 3491행, 604청크, 2014~2025 12개년**. 게이트 ✅, 비-e2e 35 passed.
- ⚠️ **옛 연도(특히 2016) 신뢰도 낮음(정직)**: integrate LLM 매핑에서 ① 서로 다른 문항/마크가 같은 std_id로 충돌(저탄소제품_인지도 값 3세트, 친환경고려_제품·추가지불의향 등) → keep-first+로그 ② 일부 이상치(환경문제_관심도 2016=69.0 vs 2017=87.9 비정상 점프 — 2016 척도/집계 차이 의심). → **'integrate 매핑 정제'(1순위↔복수응답·마크 구분, 매핑 결정화)가 후속 필수**. 2014~2015는 문항 적어 충돌 덜함.
- 코드 변경 없음(run_totalrow·dedup은 2017 라운드 커밋분 재사용). 데이터·corrections·chroma는 gitignore(로컬).

### integrate 매핑 정제 (사용자 요청) — 결정화 + 오병합 분리
- **진단**: 옛 연도 과병합 52그룹 = (A)다른 문항 오병합(저탄소제품_인지도←탄소성적표지+저탄소, 친환경제품_관심도←관심증가+관심도) (B)1순위↔복수응답 (C)매트릭스(지불의향 가격대).
- **정제(`integrate_oldyears`)**: ① **매핑 결정화** — qmap을 `std_mapping.json`에 저장/재사용(LLM 재매핑·비결정성 제거). ② **STDID_OVERRIDES**(subsection 키워드) — 관심증가↔관심도 분리(신규 친환경제품_관심증가), 저탄소제품/탄소성적표지 인지 분리. build_rows에서 적용(dedup 전).
- **클린 재구축**: 현재 dedup의 2023~25(848행)만 베이스로 두고, 옛 추출 9파일(2014~2022, 543레코드/335문항) 재통합(299 기존연결·26 신규). 587청크 재인덱싱.
- **검증**: 핵심 8개년 추세 **회귀 없음**(환경표지 인지도 2016+2018~25·그린카드 2017~25·구매경험 2016~25). 게이트 ✅, 비-e2e 35 passed.
- ⚠️ **남은 한계(정직)**: 과병합 52→51. std_mapping.json은 gitignore(로컬).

#### 매트릭스/순위 과병합 근본 진단 + 탄소계열 정밀 교정 (사용자: 근본 해소)
- **전수 분류(51)**: 순위(1순위/복수응답) 20 · 매트릭스(금액/시나리오) 5 · **기타 26** = LLM이 인지경로↔인지도↔정의인지, 고려제품↔확대희망 등 **다른 문항을 같은 std_id로 오매핑**. 즉 근본은 매트릭스/순위보다 **LLM 매핑 부정확성**(예: 탄소성적표지를 저탄소제품/환경성적표지/탄소성적표지로 제각각, 인지/경로/정의 혼합). 키워드 override는 확장성 없고 역효과(인지경로를 인지도로) 위험.
- **탄소계열 정밀 교정**: `_override_stdid`를 주제(저탄소제품 vs 환경성적표지=탄소성적표지/탄소발자국)+유형(인지경로/정의인지/인지도)로 결정하게 재작성 → 탄소계열 분리. **과병합 52→45**, 환경성적표지_인지도에 2017 연결. 599청크, 게이트 ✅, 비-e2e 35, 헤드라인 추세 회귀 없음.
- ⚠️ **남은 45(정직)**: 순위(1순위/복수응답)·매트릭스·기타 다양한 LLM 오매핑. 패턴화 불가 → **근본 해소는 std_mapping.json(335문항, 결정적·편집가능) 도메인 리뷰** 또는 추출기 매트릭스 처리 재설계. 영향은 2차 std_id 위주(헤드라인 인지도/관심도/구매경험은 깨끗). 권장: 매핑 리뷰 워크시트로 일괄 교정.

#### 매핑 리뷰 워크시트 + 이어가기 핸드오프 (2026-06-28)
- **`mapping_review.csv`(신규, 커밋)**: 남은 과병합 45그룹의 103문항. 컬럼 type/year/current_std_id/source/subsection/**proposed_std_id(편집)**/note. 순위·경로·정의 일부는 제안값 자동기입, 나머지는 빈칸(도메인 검토). **사용법**: proposed_std_id 칸을 도메인 전문가가 채움 → (다음 작업) 그 값으로 `std_mapping.json` 보정 → `2023~25 베이스 복원 후 integrate_oldyears.py --apply` 재실행(결정적, LLM無).
- **현재 라이브 상태**: 데이터 **2014~2025 12개년**, dedup 3441행(2014:148·2015:230·2016:456·2017:421·2018:221·2019:248·2020:281·2021:294·2022:294·2023:346·2024:310·2025:192), **599청크 인덱싱, 게이트 ✅**. 옛 추출 9파일은 `outputs/_staging_oldyears/`(+_integrated/ 백업), 매핑은 `std_mapping.json`(결정적). 모두 gitignore(로컬).
- **재통합 절차(메모)**: ① clean/dedup에서 year>=2023만 남겨 베이스 복원 ② `uv run python rag/integrate_oldyears.py --apply`(저장 매핑+override+dedup) ③ `rag/chunking.py`→`rag/index.py` ④ 게이트 확인.
- **다음 후보**: (a) mapping_review 채워 std_mapping.json 보정→재통합(과병합 근본해소) (b) 2013·2007 추출 (c) 신호등 12개년 확인.

### 알려진 데이터 이슈(요약)
- 표 3-60: 추출 깨짐 → corrections로 39품목 정정(사람 확정).
- 비전 후보 38건·미검수 high 33건·빈 값 다수 = **현재 인덱싱 차단 대상**(검수로 해소 필요).
- ★ **인덱스 오염**: 현재 라이브 Chroma 인덱스는 위 차단 대상 데이터로 구축됨(게이트 도입 前). 게이트는 만들었으나 인덱스를 그 기준으로 정화/재구축하지 않음 → "추측은 데이터 아님" 원칙을 실제로는 위반 중.
- 새 PDF 1개라도 standardize가 전 연도를 재처리(시간·비용).

---

## 다음 할 일 (이 대화 기반 정의, 2026-06-26)

> **원칙(CLAUDE.md): 더 만들기 전에 범위부터 합의한다. 가짜가 아닌 실제로 검증한다.**

### 0. 먼저 결정 (Think Before Coding) — 흐름 범위
사용자와 합의 필요. 셋 중 택1:
- (a) **현 스텝퍼 유지 + 실제 검증**: 실제 PDF로 인제스트를 끝까지 돌려 진행/로그/타이밍이 정말 보이고 유용한지 확인.
- (b) **최소안으로 축소**: 원래 구조(탭+사이드바 업로드)에 로깅/진행 표시만 얹기.
- (c) 절충.
→ **이 결정 전에는 신규 기능 추가하지 않는다.**

### 1. [A·최우선] 인덱스–게이트 정합 (원칙 위반 해소)
- 미확정 비전 38·미검수 high 33·빈값을 검수로 확정하거나 제외 → `validate.py` 통과 → **재인덱싱**.
- 또는 최소한 앱에 "인덱스가 미확정 데이터 포함/게이트 미통과" 경고 + 인덱스 신선도 표시.

### 2. [C] 실제 검증 + 회귀 안전망
- 🩺 로그 패널이 실제 로그를 보여주는지 Playwright 단언 추가.
- 실제 LLM 경로(검색·rerank·답변) 1건을 `@pytest.mark.slow`로 검증.
- `eval/` 평가셋(질문→기대 출처/항목) 만들어 rerank·프롬프트 회귀 정량 측정.

### 3. [B] 인제스트 견고화
- happy-path(전 단계 성공 완주)·에러 단계 복구 검증.
- `state.json` 영속화로 새로고침 후 실행 복구(현재 Popen이 session_state에만 있어 새로고침 시 추적 상실).

### 4. [D] 데이터·범위 완성
- 남은 빈칸 5(행렬형 사용후변화 등) 처리 방안.
- 옛 연도(2007, 2013~2022) 확장.
- "실시간 신호등"(추세/신호 시각화) — 아직 미착수. 데이터셋+Q&A 단계.

### 보조
- 업로드 파일명 검증(경로 탈출 방지).
- rerank 비용/지연: 필요 시 on/off·캐시.
