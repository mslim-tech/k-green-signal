# tests/e2e/test_stepper.py
# -----------------------------------------------------------------------------
# 3모드 네비(🚦 대시보드/💬 AI에게 묻기/🛠 데이터 준비) + 데이터 준비 스텝퍼:
#  - 정형 데이터가 있으면 자동으로 🚦 대시보드에 랜딩한다("결과 먼저").
#  - 🛠 데이터 준비로 들어가면 4단계 스텝퍼가 그려지고, 이동/게이트가 동작한다.
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def _goto(page: Page):
    page.goto("/")
    # 제목(h1)과 대시보드 서브헤더 둘 다 '실시간 신호등'을 포함하므로 .first 로.
    expect(page.get_by_text(re.compile("실시간 신호등")).first).to_be_visible(timeout=30000)


def _goto_prep(page: Page):
    """ 🛠 데이터 준비 모드로 진입(스텝퍼 화면). """
    _goto(page)
    page.get_by_role("button", name=re.compile("데이터 준비")).click()
    expect(page.locator("[data-testid='mode-status']")).to_have_text("prep", timeout=15000)


def test_mode_nav_and_prep_steps(page: Page, base_url: str):
    """ 샘플 데이터가 있으면 대시보드 자동 랜딩 + 3모드 버튼, prep 진입 시 4단계 스텝퍼. """
    _goto(page)
    # '결과 먼저' — 정형 데이터가 있으므로 자동으로 대시보드에 랜딩한다.
    expect(page.locator("[data-testid='mode-status']")).to_have_text("signal", timeout=15000)
    for frag in ["🚦 대시보드", "💬 AI에게 묻기", "🛠 데이터 준비"]:
        expect(page.get_by_role("button", name=re.compile(re.escape(frag)))).to_be_visible()
    # 데이터 준비로 들어가면 4단계 스텝퍼가 보이고 기본 단계는 1(업로드).
    page.get_by_role("button", name=re.compile("데이터 준비")).click()
    for frag in ["1. 📤 업로드", "2. ⚙️ 인제스트", "3. 🔍 검수", "4. 📚 인덱싱"]:
        expect(page.get_by_role("button", name=re.compile(re.escape(frag)))).to_be_visible(timeout=15000)
    expect(page.locator("[data-testid='step1-status']")).to_have_text("current")


def test_stepper_navigation(page: Page, base_url: str):
    _goto_prep(page)
    # 4단계(인덱싱)로 이동 → 준비 게이트 화면이 떠야 한다.
    page.get_by_role("button", name=re.compile(r"4\. 📚 인덱싱")).click()
    expect(page.locator("[data-testid='step4-status']")).to_have_text("current", timeout=15000)
    # 4단계로 이동하면 인덱싱 실행 버튼이 보인다(고유 요소).
    expect(page.get_by_role("button", name=re.compile("인덱싱 실행"))).to_be_visible(timeout=15000)


def test_index_integrity_status(page: Page, base_url: str):
    """ D5: 사이드 상태 패널에 인덱스 정합 상태가 표시된다(정화 후 ✅ 통과). 모드 무관(전역). """
    _goto(page)
    expect(page.get_by_text(re.compile("인덱스 정합"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("게이트 통과"))).to_be_visible(timeout=15000)


def test_step_todo_and_next_button(page: Page, base_url: str):
    """ D2: 데이터 준비의 각 단계에 '지금 할 일' 안내 + '다음 단계로' 버튼이 보인다. """
    _goto_prep(page)
    page.get_by_role("button", name=re.compile(r"3\. 🔍 검수")).click()
    expect(page.get_by_text(re.compile("지금 할 일"))).to_be_visible(timeout=15000)
    expect(page.get_by_role("button", name=re.compile("다음 단계로"))).to_be_visible(timeout=15000)


def test_index_gate_passes_after_cleanup(page: Page, base_url: str):
    """ 정화 후 준비 게이트가 통과면 '준비 완료'가 뜨고 인덱싱 버튼이 활성이어야 한다. """
    _goto_prep(page)
    page.get_by_role("button", name=re.compile(r"4\. 📚 인덱싱")).click()
    # '준비 완료'는 사이드바 다음 할 일에도 나올 수 있으므로 .first 로 단언.
    expect(page.get_by_text(re.compile("준비 완료")).first).to_be_visible(timeout=15000)
    expect(page.get_by_role("button", name=re.compile("인덱싱 실행"))).to_be_enabled(timeout=15000)
