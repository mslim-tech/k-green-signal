# tests/e2e/test_logpanel.py
# -----------------------------------------------------------------------------
# 🩺 시스템 로그 패널이 '실제 앱 로그 내용'을 보여주는지 검증한다.
#   - 기존 smoke 는 logs/app_*.log '파일'에 로그가 남는지만 봤다(서버측).
#   - 여기서는 UI 패널을 펼쳤을 때 그 로그가 화면에 실제로 노출되는지(사용자가
#     진짜로 가시성을 얻는지) 를 단언한다. → "🩺 로그 패널이 실제 로그를 보여주는지"
#     라는 다음-할-일 [C] 항목의 직접 증명.
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def test_log_panel_shows_real_app_log(page: Page, base_url: str):
    page.goto("/")
    expect(page.get_by_text(re.compile("신호등"))).to_be_visible(timeout=30000)

    # 사이드바 하단의 🩺 시스템 로그 expander 를 펼친다.
    panel = page.get_by_text(re.compile(r"🩺 시스템 로그"))
    expect(panel).to_be_visible(timeout=15000)
    panel.click()

    # 앱 로그 경로 캡션(앱 로그: …app_*.log)이 보이고,
    expect(page.get_by_text(re.compile(r"앱 로그:.*app_"))).to_be_visible(timeout=15000)

    # 코드 블록에 실제 로그 라인('앱 렌더')이 노출되어야 한다(빈/플레이스홀더가 아님).
    # (서버가 세션 공유라 렌더가 누적돼 여러 줄일 수 있으므로 .first 로 단언.)
    expect(page.get_by_text(re.compile("앱 렌더")).first).to_be_visible(timeout=15000)
    expect(page.get_by_text("(로그 비어 있음)")).to_have_count(0)
