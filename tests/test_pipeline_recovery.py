# tests/test_pipeline_recovery.py
# -----------------------------------------------------------------------------
# 인제스트 견고화(상태 영속화 + 복구)의 결정적 단위 검증 (LLM·Streamlit 불필요).
#   - save_state ↔ load_state 왕복
#   - pid_alive: 살아있는 pid(현재 프로세스) vs 없는 pid
#   - step_succeeded: 산출이 시작 이후 갱신됐는가
#   - recover_step_result: 새로고침 복구가 진행중/성공/실패로 귀결하는가
# -----------------------------------------------------------------------------

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag import pipeline


DEAD_PID = 999_999    # 거의 확실히 존재하지 않는 pid


def _use_tmp(monkeypatch, tmp_path: Path):
    """ 모듈 전역 경로(OUTPUT_DIR/STATE_FILE)를 테스트용 임시 폴더로 돌린다. """
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "STATE_FILE", tmp_path / "ingest_state.json")


def test_save_load_roundtrip(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    state = {
        "run_id": "run_x", "pdf": "a.pdf", "order": ["extract", "review"],
        "idx": 1, "status": "running", "started": {"review": 1.0},
        "ended": {}, "rc": {}, "skipped": [], "pid": {"review": 123}, "force": False,
    }
    pipeline.save_state(state)
    assert pipeline.load_state() == state


def test_load_state_none_when_missing(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    assert pipeline.load_state() is None


def test_pid_alive():
    assert pipeline.pid_alive(os.getpid()) is True
    assert pipeline.pid_alive(DEAD_PID) is False
    assert pipeline.pid_alive(None) is False


def test_step_succeeded(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    step = pipeline.STEP_BY_KEY["review"]      # produces review_queue.csv
    out = pipeline.step_output(step, None)

    # 산출이 없으면 실패
    assert pipeline.step_succeeded(step, None, time.time()) is False

    # 산출이 시작 이후 갱신됐으면 성공
    started = time.time()
    out.write_text("ok", encoding="utf-8")
    assert pipeline.step_succeeded(step, None, started) is True

    # 시작이 산출보다 한참 미래(=산출이 옛것)면 실패
    assert pipeline.step_succeeded(step, None, time.time() + 100) is False


def test_recover_step_result(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    step = pipeline.STEP_BY_KEY["review"]
    out = pipeline.step_output(step, None)

    # pid 가 살아있으면 아직 진행 중 → None
    assert pipeline.recover_step_result(step, None, os.getpid(), time.time()) is None

    # pid 죽음 + 산출 존재(시작 이후) → 'ok'
    started = time.time()
    out.write_text("done", encoding="utf-8")
    assert pipeline.recover_step_result(step, None, DEAD_PID, started) == "ok"

    # pid 죽음 + 산출 없음 → 'fail'
    out.unlink()
    assert pipeline.recover_step_result(step, None, DEAD_PID, time.time()) == "fail"
