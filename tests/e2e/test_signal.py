# tests/e2e/test_signal.py
# -----------------------------------------------------------------------------
# 실시간 신호등(6단계): 단계로 이동하면 추세 신호 화면이 그려지는지 확인.
#   - 상승/보합/하락 집계 + '가장 큰 변화' + '카테고리별 추세'가 보인다.
#   (LLM 불필요 — 정형 데이터(outputs CSV)만으로 렌더. RAG_FAKE_LLM 무관.)
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def _goto(page: Page):
    page.goto("/")
    expect(page.get_by_text(re.compile("실시간 신호등"))).to_be_visible(timeout=30000)


def test_signal_step_shows_trends(page: Page, base_url: str):
    _goto(page)
    page.get_by_role("button", name=re.compile(r"6\. 🚦 신호등")).click()
    expect(page.locator("[data-testid='step6-status']")).to_have_text("current", timeout=15000)
    # 신호등 화면의 고유 섹션들
    expect(page.get_by_text(re.compile("가장 큰 변화"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("카테고리별 추세"))).to_be_visible(timeout=15000)
    # 집계 카드(상승/보합/하락 중 하나라도)
    expect(page.get_by_text(re.compile("상승")).first).to_be_visible(timeout=15000)
