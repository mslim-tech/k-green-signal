# 작업 로그 (logging.md)

> 사용자 요청과 그에 대한 작업 내용을 날짜별로 정리하는 파일.
> "내가 무엇을 요청했고, 무엇이 어떻게 처리됐는지"를 추적한다.

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
- 수정: `app.py`(검수·RAG 탭, 다중 업로드, 로깅 연결), `rag/dedup.py`(section 매칭), `README.md`, `plan.md`, `pyproject.toml`(dev: pytest·playwright + pytest 설정), `.gitignore`(logs/·test-results/).

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

### 결과
- 가이드 스텝퍼 + 검증/로깅 하네스 완성. `uv run pytest tests/e2e` 7 passed(RAG_FAKE_LLM 결정적).
- 사용자 요구 충족: 업로드 상태/다음단계 안내, 🩺 시스템 로그 가시화, 인제스트 진행/단계 소요시간, 답변 지연 원인(검색 vs 생성) 표시, 문서Q&A·데이터질의 통합, 엄격 준비 게이트.

### 알려진 데이터 이슈(요약)
- 표 3-60: 추출 깨짐 → corrections로 39품목 정정(사람 확정). 
- 비전 후보 38건·미검수 high 33건·빈 값 다수 = **현재 인덱싱 차단 대상**(검수로 해소 필요).
- 새 PDF 1개라도 standardize가 전 연도를 재처리(시간·비용) — UI에 명시 예정.
