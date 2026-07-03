# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 대한민국 친환경 소비 인지도 실시간 신호등. 친환경 인지도 조사 PDF를
> 근거 기반 정형 데이터셋으로 통합하고 그 위에 RAG 질의응답을 올린다.

---

## 명령어 (Commands)

패키지 관리는 **uv** (venv `.venv/`). 파이썬 3.12+. `Makefile` 없음 — 아래 명령을 직접 쓴다.

```bash
uv sync                                    # 의존성 설치
uv run streamlit run app.py                # 앱 실행(가이드 스텝퍼)

# 테스트 — 기본은 'not slow'(빠르고 결정적, LLM 미호출)만 돈다(pyproject addopts)
uv run pytest                              # 전체(단위 + E2E, slow 제외)
uv run pytest tests/test_signals.py -q     # 단위 파일 하나
uv run pytest tests/test_signals.py::test_name   # 테스트 하나
uv run pytest -m slow                      # 실제 OpenAI 호출 테스트만(과금·비결정적)
uv run playwright install chromium         # E2E 최초 1회
uv run pytest tests/e2e -v                 # Playwright E2E(서버 자동 기동/종료)

# 데이터 파이프라인 — 각 rag/*.py 는 독립 실행 가능한 CLI(main)
uv run python -m rag.ingest.ingestion                            # 0 진단
uv run python -m rag.ingest.extract "data/<파일>.pdf" 999 --save # 2 추출
uv run python -m rag.transform.standardize                          # 3 표준화
uv run python -m rag.transform.refine && uv run python -m rag.transform.dedup \
  && uv run python -m rag.transform.flags && uv run python -m rag.transform.review   # 4 정제·검수큐
uv run python -m rag.curate.validate                             # 인덱싱 준비 게이트(차단 항목 표시)
uv run python -m rag.retrieval.chunking && uv run python -m rag.retrieval.index     # 6 청킹·Chroma 인덱싱
```

### 실행 환경변수(env)
- `OPENAI_API_KEY` — `.env`에서만 읽는다(코드/문서에 직접 쓰지 않음).
- `RAG_FAKE_LLM` — 세팅 시 LLM 호출을 결정적 스텁으로 대체(추출·답변 스텁, rerank 생략). E2E·단위 테스트가 이걸로 무료·결정적 실행. **UI 배선만 검증** — 실제 동작 확인이 필요하면 스텁 없이 실행.
- `RAG_OUTPUT_DIR` — 산출물 디렉터리(기본 `outputs`). E2E는 임시 복사본을 가리켜 실제 `outputs/`를 건드리지 않는다.
- `RAG_LOG_DIR`(기본 `logs`) · `RAG_LOG_LEVEL`(기본 `INFO`).

---

## 아키텍처 (Architecture)

전체 설계는 [`ARCHITECTURE.md`](./docs/ARCHITECTURE.md)(5레이어 설계서)와 [`README.md`](./README.md)(파이프라인 표), 진행은 [`PLAN.md`](./docs/PLAN.md), 작업 로그는 [`LOGGING.md`](./docs/LOGGING.md), 결정 기록은 [`DECISIONS.md`](./docs/DECISIONS.md) 참고. 여기서는 여러 파일을 읽어야 보이는 큰 그림만.

**핵심 데이터 흐름 = 산출물 파일 체인** (모든 단계가 이전 단계 CSV/JSONL을 읽어 새 파일을 쓴다 — `outputs/` 아래, 원본 보존):

```
data/*.pdf
  → parsing.py           문항 블록 분리(+page/출처)
  → extract.py           블록→구조화 레코드   → outputs/*.extracted.jsonl
     extract_vision.py   표 블록은 페이지 이미지 멀티모달 판독(라우팅: routing.py)
  → standardize.py       연도별 문항→표준 std_id로 통합(std_aliases.py) → standardized_long.clean.csv
  → refine.py            라벨 표준화
  → dedup.py             중복 제거/과잉병합 분리 → standardized_long.dedup.csv
  → flags.py             의심값 자동 플래그(급변/서술정합/합계100) → standardized_long.flagged.csv
  → review.py            저신뢰·플래그 행 → review_queue.csv
  → [사람 검수] app.py + corrections.py → corrections.jsonl (확정값 오버레이)
  → validate.py          준비 게이트: 빈/미확정/미검수면 인덱싱 차단
  → chunking.py          A(문항-서술)·B(정형-사실) 청크 + 출처메타 → chunks.jsonl
  → index.py             임베딩 → Chroma(outputs/chroma/)
  → retriever.py → answer.py   벡터검색 → LLM rerank → 출처 인용 답변
```

**두 개의 흐름 — 이 프로젝트의 중심 불변식:**
- **실선(확정 데이터)**: 충실히 추출된 사실값만 정형 CSV로 흐른다.
- **점선(추측 격리)**: 비전 불일치·저신뢰·플래그는 데이터가 아니라 **검토 큐**(`vision_candidates.csv`·`review_queue.csv`)로 간다. 사람이 원문을 보고 `corrections.jsonl`로 확정한 것만 실선에 오버레이된다. → 새 코드가 휴리스틱/LLM 판단을 정형 CSV에 직접 쓰면 원칙 위반이다.

**신호등 레이어**: `signals.py`는 LLM 없는 순수 함수 — 정형 사실 행을 (문항, 응답라벨)별 연도 시계열로 묶어 최신 YoY(%p)로 🟢상승/🟡보합/🔴하락 신호를 매긴다(색은 가치판단 아닌 방향만, 추정/보간 없음).

**설정·경로·모델의 단일 지점:**
- 모델명은 전부 `rag/core/config.py`에서만(현재 생성계열 `gpt-5.4-mini`, 임베딩 `text-embedding-3-small`). 파일마다 하드코딩 금지.
- 산출물 경로는 `rag/core/paths.py`의 `OUTPUT_DIR`(env override).
- 각 `rag/*.py`는 `from rag.x import ...`(패키지) / `from x import ...`(직접 실행) 이중 import를 try/except로 지원 — 새 모듈도 이 패턴을 따른다.

**앱 오케스트레이션**: `app.py`(가이드 스텝퍼: 업로드→인제스트→검수→인덱싱→질의 + 🩺 시스템 로그)는 긴 LLM 단계를 `rag/pipeline.py`로 **서브프로세스** 실행해 Streamlit을 막지 않고 로그를 단계별 캡처한다(Popen은 `st.session_state`에 보관).

---

## 행동 원칙 (코딩 가이드라인)

출처: <https://github.com/multica-ai/andrej-karpathy-skills/blob/main/CLAUDE.md>
LLM 보조의 흔한 실수(불필요한 변경·과설계·뒤늦은 질문)를 줄이기 위한 4원칙. **이 원칙이 아래 프로젝트 규칙과 충돌하면 이 원칙을 우선한다.**

1. **Think Before Coding — 먼저 생각하고, 혼란을 숨기지 마라.**
   "Don't assume. Don't hide confusion." 해석이 여러 개면 임의로 하나 고르지 말고 제시한다.
   불명확하거나 **요청 범위가 애매하면 구현 전에 멈추고 묻는다.** (작은 요청을 큰 재설계로 키우지 않는다.)

2. **Simplicity First — 단순함 우선.**
   "Minimum code that solves the problem. Nothing speculative." 요청한 것만 푸는 최소 코드.
   요청하지 않은 기능·불필요한 추상화·미래를 위한 유연성은 넣지 않는다.

3. **Surgical Changes — 외과적 변경.**
   "Touch only what you must. Clean up only your own mess." 꼭 바꿔야 할 것만 바꾼다.
   무관한 코드를 개선하지 말고, 기존 스타일을 따르며, 내 변경으로 불필요해진 import/변수만 정리한다.

4. **Goal-Driven Execution — 목표 기반 실행.**
   구현 전에 **검증 가능한 성공 기준**을 정한다. "버그 고쳐" → "재현 테스트를 쓰고 통과시킨다".
   구현 후에는 그 기준으로 실제 확인(테스트/실행)한 뒤 마친다.

---

## 보안
- API Key는 `.env`의 `OPENAI_API_KEY`에서만 읽는다. 코드/문서에 절대 직접 쓰지 않는다.
- `.env`·`data/`(작업 폴더 원본 PDF)·`outputs/`(작업 폴더 산출물)·`logs/`·`test-results/`는 커밋하지 않는다(`.gitignore`, 루트 고정 `/data/`·`/outputs/`).
- **클론 즉시 재현**: 산출물(정형 CSV·청크·**Chroma 인덱스**) 레퍼런스 사본은 `samples/`에 커밋한다(작업 폴더와 분리 → 재사용자 충돌 0). 원본 PDF는 용량상 제외한다(`.gitignore: samples/data/*.pdf` — 공개 official 보고서, 출처는 `samples/data/README.md`; 결과 보기엔 PDF 불필요). 클론 후 `uv run python scripts/bootstrap_samples.py`가 `samples/`→작업 폴더로 펼친다(신호등·검색은 키 없이 동작, RAG 답변 생성만 각자 키). `.gitignore`의 `/data/`·`/outputs/`는 루트 고정이라 `samples/` 하위 레퍼런스는 추적된다.
- 외부에 푸시하기 전 비밀·원본 데이터 노출 여부를 점검한다.

## 프로젝트 구조
- Streamlit 앱 진입점은 `app.py`로 둔다. (현재: 가이드 스텝퍼 — 업로드→인제스트→검수→인덱싱→질의)
- 처음에는 `app.py` 하나로 시작하되, 기능이 늘면 기능별 파일로 분리한다.
- 문서 진단 `rag/ingest/ingestion.py` · Chunking `rag/retrieval/chunking.py` · Vector DB `rag/retrieval/index.py` · 검색 `rag/retrieval/retriever.py`.
- 결과 파일이 필요해지는 시점에 `outputs/`, 평가 질문은 `eval/`, 샘플/원본 문서는 `data/`.

## Metadata 규칙
각 Chunk metadata에는 최소한 다음 항목을 유지한다.
- source · page · parser_type · chunk_id · token_count · warning

## 데이터 원칙
- **"추측은 데이터가 아니다."** 문서에 실제로 있는 것만 출처(page/표번호/구절)와 함께 DB에 넣는다.
- LLM·휴리스틱·비전의 불확실한 결과는 데이터가 아니라 **검토 후보**로만 두고, 사람이 출처를 보고 확정(`corrections.jsonl`)한 것만 인덱싱한다(엄격 게이트 `rag/curate/validate.py`).
- 답변은 검색된 근거에 grounding 하고 출처를 인용한다. 근거가 없으면 "문서에서 찾을 수 없습니다".

## 작업 방식
- 큰 변경 전에는 먼저 계획·파일 구조를 제안하고 **사용자 승인 뒤** 구현한다. (범위는 미리 합의 — 행동 원칙 1)
- 한 번에 전체를 구현하지 말고 단계별로, **각 증분마다 검증(테스트/실행)한 뒤** 다음으로 넘어간다.
- 코드는 비전공자가 읽기 쉽게 쓰고, 각 파일 상단에 그 파일의 역할을 주석으로 적는다.
- UI/동작 변경은 가능하면 Playwright E2E(`tests/e2e`)로 의도대로 동작하는지 확인한다.
  단, 가짜 모드(`RAG_FAKE_LLM`)는 UI 배선만 검증하므로, 실제 동작 검증이 필요하면 실제 실행으로 확인한다.
