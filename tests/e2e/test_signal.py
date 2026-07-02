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
    # 기본 탭(추세 신호)의 고유 섹션들
    expect(page.get_by_text(re.compile("가장 큰 변화"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("카테고리별 추세"))).to_be_visible(timeout=15000)
    # 집계 카드(상승/보합/하락 중 하나라도)
    expect(page.get_by_text(re.compile("상승")).first).to_be_visible(timeout=15000)


def test_signal_core_tab_shows_priority_indicators(page: Page, base_url: str):
    _goto(page)
    page.get_by_role("button", name=re.compile(r"6\. 🚦 신호등")).click()
    expect(page.locator("[data-testid='step6-status']")).to_have_text("current", timeout=15000)
    # '핵심 정책 지표' 탭으로 전환 → 우선 지표 그룹 제목과 실제 지표가 보인다.
    page.get_by_role("tab", name=re.compile("핵심 정책 지표")).click()
    expect(page.get_by_text(re.compile("주요 인증제도 인지도 추이"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile(r"환경표지\(마크\) 인지도")).first).to_be_visible(timeout=15000)


def test_signal_phase2_tabs_render(page: Page, base_url: str):
    _goto(page)
    page.get_by_role("button", name=re.compile(r"6\. 🚦 신호등")).click()
    expect(page.locator("[data-testid='step6-status']")).to_have_text("current", timeout=15000)
    # 2단계 탭들: 판단 기준(누적막대)·인지 경로(히트맵)·구매 장벽(파레토)이 각각 그려진다.
    page.get_by_role("tab", name=re.compile("판단 기준")).click()
    expect(page.get_by_text(re.compile("일관 라벨")).first).to_be_visible(timeout=15000)
    page.get_by_role("tab", name=re.compile("인지 경로")).click()
    expect(page.get_by_text(re.compile("연도×경로 히트맵"))).to_be_visible(timeout=15000)
    page.get_by_role("tab", name=re.compile("구매 장벽")).click()
    expect(page.get_by_text(re.compile("파레토"))).to_be_visible(timeout=15000)


def test_signal_query_filter_narrows_items(page: Page, base_url: str):
    _goto(page)
    page.get_by_role("button", name=re.compile(r"6\. 🚦 신호등")).click()
    expect(page.locator("[data-testid='step6-status']")).to_have_text("current", timeout=15000)
    expect(page.get_by_text(re.compile("가장 큰 변화"))).to_be_visible(timeout=15000)
    # 질문/키워드를 입력하면 '필터' 캡션이 뜨고 해당 항목만 남는다.
    box = page.get_by_placeholder(re.compile("환경표지 인지도"))
    box.fill("그린카드")
    box.press("Enter")
    expect(page.get_by_text(re.compile(r"🔎 '그린카드' 필터"))).to_be_visible(timeout=15000)


def test_signal_query_summary_panel(page: Page, base_url: str):
    """ 검색어를 넣으면 상단에 '결론 먼저' 핵심 요약(신호 집계 + 상승/하락 Top3)이 뜨고,
        아래는 '상세 근거'로 기존 차트가 남는다. (근거 있는 signals 만으로 구성) """
    _goto(page)
    page.get_by_role("button", name=re.compile(r"6\. 🚦 신호등")).click()
    expect(page.locator("[data-testid='step6-status']")).to_have_text("current", timeout=15000)
    box = page.get_by_placeholder(re.compile("환경표지 인지도"))
    box.fill("환경표지")
    box.press("Enter")
    expect(page.get_by_text(re.compile(r"핵심 요약 \(결론\)"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("지표별 현재 성적표"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("가장 크게 상승 Top3"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("가장 크게 하락 Top3"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("행동 동기"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("변곡점 × 외부 맥락"))).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("상세 근거"))).to_be_visible(timeout=15000)
