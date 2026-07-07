# rag/core/config.py
# -----------------------------------------------------------------------------
# 프로젝트 전역 모델 설정 (한 곳에서 관리)
#
# 이 파일의 역할:
#   - 어떤 작업에 어떤 OpenAI 모델을 쓸지 한 곳에 모아둔다.
#   - 모델을 바꿀 때는 이 파일만 고치면 모든 단계(app.py, extract.py, standardize.py …)에
#     반영된다. (예전엔 gpt-4o 문자열이 파일마다 흩어져 있었다.)
#
# 보안: 여기에는 모델 '이름'만 적는다. API Key 는 절대 적지 않고, 각 모듈이 .env 에서 읽는다.
#
# 모델 선택 메모(2026-06 기준, OpenAI models API 로 사용 가능 확인함):
#   - gpt-4o(2024) 는 오래되어 gpt-5.4-mini(2026-03) 로 교체.
#   - gpt-5.4-mini 는 temperature=0 + Structured Outputs 정상 지원(확인 완료).
# -----------------------------------------------------------------------------

# --- 생성·판단 작업에 쓰는 채팅 모델 (현재 모두 gpt-5.4-mini 로 통일) ----------
ANSWER_MODEL = "gpt-5.4-mini"      # RAG / Baseline 답변
REWRITE_MODEL = "gpt-5.4-mini"     # 질문 재작성 (query rewriting)
RERANKER_MODEL = "gpt-5.4-mini"    # 검색 결과 재정렬 (reranker)
EXAMPLE_Q_MODEL = "gpt-5.4-mini"   # 예시 질문 생성
VISION_MODEL = "gpt-5.4-mini"      # Vision (차트/그림 이미지 판독 등)
WEB_SEARCH_MODEL = "gpt-5.4-mini"  # 외부 맥락 실 웹검색(Responses API web_search 툴)
                                   #   web_search 툴 지원 확인 완료(2026-07-07, 실호출 검증).
                                   #   미지원 모델로 바꿀 땐 이 상수만 검색 지원 모델로 교체.

# --- 데이터 파이프라인(문항 추출/표준화) 작업 -------------------------------
EXTRACT_MODEL = "gpt-5.4-mini"     # 2단계 LLM 구조화 추출
STANDARDIZE_MODEL = "gpt-5.4-mini" # 3단계 문항 표준화

# --- 인덱싱·검색 임베딩 -------------------------------------------------------
EMBEDDING_MODEL = "text-embedding-3-small"

# --- LLM 병렬 처리 워커 수 (extract·refine 등 블록/문항 단위 병렬 호출) --------
#   너무 크면 OpenAI rate limit(429)에 걸려 오히려 재시도로 느려진다.
#   계정 한도가 빡빡해 보수적으로 4 로 둔다(필요 시 여기서만 조정).
LLM_MAX_WORKERS = 4
