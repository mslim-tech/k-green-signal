# tests/e2e/test_stepper.py
# -----------------------------------------------------------------------------
# 가이드 스텝퍼: 5단계 네비가 그려지고, 단계 이동이 동작하며,
# 인덱싱 단계(4)에 준비 게이트가 표시되는지 확인한다.
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def _goto(page: Page):
    page.goto("/")
    expect(page.get_by_text(re.compile("신호등"))).to_be_visible(timeout=30000)


def test_stepper_shows_five_steps(page: Page, base_url: str):
    _goto(page)
    for frag in ["1. 📤 업로드", "2. ⚙️ 인제스트", "3. 🔍 검수", "4. 📚 인덱싱", "5. 💬 질의"]:
        expect(page.get_by_role("button", name=re.compile(re.escape(frag)))).to_be_visible()
    # 기본 단계는 1(업로드) — 상태 센티넬 확인(숨김 요소라도 textContent 로 단언 가능)
    expect(page.locator("[data-testid='step1-status']")).to_have_text("current")


def test_stepper_navigation(page: Page, base_url: str):
    _goto(page)
    # 4단계(인덱싱)로 이동 → 준비 게이트 화면이 떠야 한다.
    page.get_by_role("button", name=re.compile(r"4\. 📚 인덱싱")).click()
    expect(page.locator("[data-testid='step4-status']")).to_have_text("current", timeout=15000)
    # 4단계로 이동하면 인덱싱 실행 버튼이 보인다(고유 요소).
    expect(page.get_by_role("button", name=re.compile("인덱싱 실행"))).to_be_visible(timeout=15000)


def test_index_integrity_warning(page: Page, base_url: str):
    """ D5: 현재 데이터가 게이트 미통과면 사이드 상태 패널에 인덱스 정합 경고가 뜬다. """
    _goto(page)
    expect(page.get_by_text(re.compile("인덱스 정합"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("미통과"))).to_be_visible(timeout=15000)


def test_step_todo_and_next_button(page: Page, base_url: str):
    """ D2: 각 단계에 '지금 할 일' 안내 + '다음 단계로' 버튼이 보인다. """
    _goto(page)
    page.get_by_role("button", name=re.compile(r"3\. 🔍 검수")).click()
    expect(page.get_by_text(re.compile("지금 할 일"))).to_be_visible(timeout=15000)
    expect(page.get_by_role("button", name=re.compile("다음 단계로"))).to_be_visible(timeout=15000)


def test_index_gate_blocks_button(page: Page, base_url: str):
    """ 준비 게이트가 미달이면(현재 데이터는 차단 상태) 인덱싱 버튼이 비활성이어야 한다. """
    _goto(page)
    page.get_by_role("button", name=re.compile(r"4\. 📚 인덱싱")).click()
    # 차단 요약이 뜨고
    expect(page.get_by_text(re.compile("인덱싱 차단"))).to_be_visible(timeout=15000)
    # 인덱싱 실행 버튼은 비활성(엄격 게이트)
    expect(page.get_by_role("button", name=re.compile("인덱싱 실행"))).to_be_disabled(timeout=15000)
