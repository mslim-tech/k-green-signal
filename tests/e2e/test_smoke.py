# tests/e2e/test_smoke.py
# -----------------------------------------------------------------------------
# Smoke: 앱이 실제 브라우저 세션에서 로드되고 제목이 보이는지, 그리고
#        앱 렌더 시 app 로그가 파일로 남는지(증분1 로깅의 앱-렌더 검증)를 확인한다.
# -----------------------------------------------------------------------------

import re
import time
from pathlib import Path

from playwright.sync_api import Page, expect

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"


def test_app_loads_and_title_visible(page: Page, base_url: str):
    page.goto("/")
    # Streamlit 스크립트가 실제로 실행되면 제목 h1 이 그려진다.
    expect(page.get_by_text(re.compile("RAG Lab"))).to_be_visible(timeout=30000)


def test_app_render_writes_log(page: Page, base_url: str):
    """ 브라우저 세션이 main() 을 실행 → logs/app_*.log 에 '앱 렌더' 이벤트가 남아야 한다. """
    page.goto("/")
    expect(page.get_by_text(re.compile("RAG Lab"))).to_be_visible(timeout=30000)

    # 로그 flush 여유
    deadline = time.monotonic() + 10
    found = False
    while time.monotonic() < deadline and not found:
        logs = sorted(LOG_DIR.glob("app_*.log"))
        for lf in logs:
            try:
                if "앱 렌더" in lf.read_text(encoding="utf-8"):
                    found = True
                    break
            except Exception:
                pass
        if not found:
            time.sleep(1)
    assert found, "logs/app_*.log 에 '앱 렌더' 로그가 보이지 않음 (앱 로깅 미작동)"
