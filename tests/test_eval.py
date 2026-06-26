# tests/test_eval.py
# -----------------------------------------------------------------------------
# eval/ 평가셋을 실제 LLM 으로 돌려 '회귀 게이트'로 쓴다(기본 skip, -m slow 에서만).
#   rerank·프롬프트·인덱스를 바꿨을 때 정답 grounding 이 깨지면 여기서 빨갛게 잡힌다.
#
#   실행:  uv run pytest tests/test_eval.py -m slow -q
# -----------------------------------------------------------------------------

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHROMA_DIR = PROJECT_ROOT / "outputs" / "chroma"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.slow
def test_eval_set_all_pass(monkeypatch):
    monkeypatch.delenv("RAG_FAKE_LLM", raising=False)

    if not CHROMA_DIR.exists():
        pytest.skip("outputs/chroma 인덱스가 없어 평가 불가 (먼저 인덱싱 필요)")
    if not os.getenv("OPENAI_API_KEY"):
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY 없음 — 평가 skip")

    from eval.run_eval import run

    report = run()
    failed = [r["id"] for r in report["results"] if not r["passed"]]
    assert not failed, (
        f"평가 {report['passed']}/{report['total']} 통과 — 실패: {failed}\n"
        + "\n".join(
            f"  {r['id']}: {r['fails']}" for r in report["results"] if not r["passed"]
        )
    )
