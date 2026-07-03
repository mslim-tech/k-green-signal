# tests/test_real_llm.py
# -----------------------------------------------------------------------------
# 실제 LLM 경로 검증 (기본 skip, `-m slow` 또는 `--no-header -m slow` 로만 실행).
#
#   기존 E2E 는 전부 RAG_FAKE_LLM(가짜 답변)이라 'UI 배선'만 증명했다.
#   이 테스트는 실제 OpenAI 로 검색→리랭크→근거인용 답변을 한 번 돌려, 파이프라인이
#   진짜로 정답 청크를 찾아 출처를 인용하는지(grounding) 를 단언한다.
#
#   전제: outputs/chroma 인덱스가 로컬에 있어야 하고, .env 에 OPENAI_API_KEY 가 있어야 한다.
#         (둘 중 하나라도 없으면 skip — CI 등에서 비용 없이 통과.)
#
#   실행:  uv run pytest tests/test_real_llm.py -m slow -q
# -----------------------------------------------------------------------------

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHROMA_DIR = PROJECT_ROOT / "outputs" / "chroma"

# in-process 로 rag 패키지를 import 하므로 프로젝트 루트를 경로에 둔다.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.slow
def test_real_answer_grounds_and_cites(monkeypatch):
    # 실제 LLM 경로여야 하므로 가짜 모드 강제 해제.
    monkeypatch.delenv("RAG_FAKE_LLM", raising=False)

    if not CHROMA_DIR.exists():
        pytest.skip("outputs/chroma 인덱스가 없어 실제 검증 불가 (먼저 인덱싱 필요)")
    if not os.getenv("OPENAI_API_KEY"):
        # .env 는 get_client 가 로드하지만, 키 자체가 없으면 skip.
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY 없음 — 실제 LLM 검증 skip")

    from rag.retrieval.answer import answer

    # 안정적인 단일 사실 질문으로 실제 경로(연도 자동감지 → 검색 → 근거인용)를 검증한다.
    #   원문: 2024 그린카드 인지도 = '알고 있다' 62.6% (p.107-108).
    #   ('연도 자동감지'가 없으면 2024 청크가 rerank 창 밖으로 밀려 못 찾던 회귀를 막는다.)
    #   (두 비슷한 표 구분 같은 LLM 변동성 큰 케이스는 게이트로 삼지 않는다 — 아래 주석/문서 참고.)
    res = answer("2024년 그린카드 인지도는?", k=5)

    # (1) 근거 인용 형식이 있어야 한다.
    assert "[출처:" in res.text, f"출처 인용이 없음:\n{res.text}"

    # (2) 실제 정답 수치(62.6%)에 grounding 되어야 한다(환각/엉뚱한 답이 아님).
    assert "62.6" in res.text, f"정답(62.6%)이 답변에 없음:\n{res.text}"

    # (3) 검색이 정답 청크를 실제로 가져왔는지(연도 자동감지로 2024 로 좁힘).
    assert res.hits, "검색 결과가 비어 있음 (인덱스/임베딩 문제)"
    years = {str(h.metadata.get("year")) for h in res.hits}
    assert years == {"2024"}, f"연도 자동감지가 2024 로 좁히지 못함 (검색된 연도: {years})"

    # (5) 단계별 소요시간이 실제로 측정됐는지(검색·생성 둘 다 > 0).
    assert res.timings.get("retrieval", 0) > 0, "검색 시간이 0 (실제 검색 미수행)"
    assert res.timings.get("generate", 0) > 0, "생성 시간이 0 (실제 LLM 미호출)"
