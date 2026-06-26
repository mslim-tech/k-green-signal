# k-green-signal — 프로젝트 규칙 (CLAUDE.md)

> 대한민국 친환경 소비 인지도 실시간 신호등. 친환경 인지도 조사 PDF를
> 근거 기반 정형 데이터셋으로 통합하고 그 위에 RAG 질의응답을 올린다.

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
- `.env`·`data/`(원본 PDF)·`outputs/`(산출물)·`logs/`·`test-results/`는 커밋하지 않는다(`.gitignore`).
- 외부에 푸시하기 전 비밀·원본 데이터 노출 여부를 점검한다.

## 프로젝트 구조
- Streamlit 앱 진입점은 `app.py`로 둔다. (현재: 가이드 스텝퍼 — 업로드→인제스트→검수→인덱싱→질의)
- 처음에는 `app.py` 하나로 시작하되, 기능이 늘면 기능별 파일로 분리한다.
- 문서 진단 `rag/ingestion.py` · Chunking `rag/chunking.py` · Vector DB `rag/index.py` · 검색 `rag/retriever.py`.
- 결과 파일이 필요해지는 시점에 `outputs/`, 평가 질문은 `eval/`, 샘플/원본 문서는 `data/`.

## Metadata 규칙
각 Chunk metadata에는 최소한 다음 항목을 유지한다.
- source · page · parser_type · chunk_id · token_count · warning

## 데이터 원칙
- **"추측은 데이터가 아니다."** 문서에 실제로 있는 것만 출처(page/표번호/구절)와 함께 DB에 넣는다.
- LLM·휴리스틱·비전의 불확실한 결과는 데이터가 아니라 **검토 후보**로만 두고, 사람이 출처를 보고 확정(`corrections.jsonl`)한 것만 인덱싱한다(엄격 게이트 `rag/validate.py`).
- 답변은 검색된 근거에 grounding 하고 출처를 인용한다. 근거가 없으면 "문서에서 찾을 수 없습니다".

## 작업 방식
- 큰 변경 전에는 먼저 계획·파일 구조를 제안하고 **사용자 승인 뒤** 구현한다. (범위는 미리 합의 — 행동 원칙 1)
- 한 번에 전체를 구현하지 말고 단계별로, **각 증분마다 검증(테스트/실행)한 뒤** 다음으로 넘어간다.
- 코드는 비전공자가 읽기 쉽게 쓰고, 각 파일 상단에 그 파일의 역할을 주석으로 적는다.
- UI/동작 변경은 가능하면 Playwright E2E(`tests/e2e`)로 의도대로 동작하는지 확인한다.
  단, 가짜 모드(`RAG_FAKE_LLM`)는 UI 배선만 검증하므로, 실제 동작 검증이 필요하면 실제 실행으로 확인한다.
