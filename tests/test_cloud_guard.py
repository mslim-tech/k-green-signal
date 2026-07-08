# tests/test_cloud_guard.py
# -----------------------------------------------------------------------------
# 배포(웹) 가드의 판정 함수 is_cloud() 단위 검증.
#   - 로컬(개발/CI) 경로는 /mount/src 가 아니므로 기본 False
#   - RAG_FORCE_CLOUD 훅이 세팅되면 강제 True (E2E·수동 검증용)
# 이 판정으로 배포 웹에서 인제스트(std_id 재배정·휘발) 실행을 막는다.
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.common import is_cloud


def test_is_cloud_false_on_local(monkeypatch):
    # 테스트는 로컬/CI 에서 도므로(경로가 /mount/src 아님) 기본은 False.
    monkeypatch.delenv("RAG_FORCE_CLOUD", raising=False)
    assert is_cloud() is False


def test_is_cloud_forced_by_env(monkeypatch):
    # 검증/E2E 훅: 환경변수가 있으면 강제로 클라우드로 판정.
    monkeypatch.setenv("RAG_FORCE_CLOUD", "1")
    assert is_cloud() is True
