# tests/e2e/test_rag.py
# -----------------------------------------------------------------------------
# 💬 AI에게 묻기 모드:
#   - 질문하면 출처 인용([출처:)이 포함된 답변과 처리 시간이 보인다.
#   - 근거 출처 expander 에 스텁 근거가 카드로 보인다(없는 PDF 라 페이지 토글은 미노출).
#   - advise 모드는 스텁의 헤딩 계약이 KEEP/ADD/DROP/FIX 카드로 구조화 렌더된다.
# (RAG_FAKE_LLM=1 → 답변·근거는 결정적 스텁. UI 배선을 검증한다.)
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def _goto_qa(page: Page):
    """ 💬 AI에게 묻기 모드로 진입 — 전환 완료를 센티넬로 기다린다(랜딩 신호등 화면의
        '🔎 질문/키워드' 입력이 '질문' 정규식에 먼저 걸리는 경합 방지). """
    page.goto("/")
    expect(page.get_by_text(re.compile("실시간 신호등")).first).to_be_visible(timeout=30000)
    page.get_by_role("button", name=re.compile("AI에게 묻기")).click()
    expect(page.locator("[data-testid='mode-status']")).to_have_text("qa", timeout=15000)


def _ask(page: Page, question: str):
    box = page.get_by_role("textbox", name="질문", exact=True)
    expect(box).to_be_visible(timeout=15000)
    box.fill(question)
    box.press("Enter")


def test_rag_answer_has_citation_and_timing(page: Page, base_url: str):
    _goto_qa(page)
    _ask(page, "녹색제품 인지율은?")

    # 근거 인용 표기 + 처리 시간(왜 느린지) 표시 — 스텁 답변의 인용을 단언
    expect(page.get_by_text(re.compile(r"\[출처:")).first).to_be_visible(timeout=20000)
    expect(page.get_by_text(re.compile("처리 시간"))).to_be_visible(timeout=20000)


def test_rag_sources_expander_shows_hit_card(page: Page, base_url: str):
    """ 근거 출처 expander: 스텁 근거 1건이 카드(연도·std_id·유사도)로 보이고,
        존재하지 않는 PDF(sample.pdf)라 '원문 페이지 보기' 토글은 노출되지 않는다. """
    _goto_qa(page)
    _ask(page, "녹색제품 인지율은?")

    page.get_by_text(re.compile("근거 출처 1건")).click()
    expect(page.get_by_text(re.compile(r"sample\.pdf")).first).to_be_visible(timeout=15000)
    expect(page.get_by_text(re.compile("유사도"))).to_be_visible(timeout=15000)
    # 폴백 경로 증명: PDF 가 없으면 토글 자체가 없어야 한다(죽은 UI 금지).
    expect(page.get_by_text(re.compile("원문 페이지 보기"))).to_have_count(0)


def test_rag_advise_mode_structured_sections(page: Page, base_url: str):
    """ advise 모드: 스텁의 헤딩 계약이 갈래별 카드(KEEP/ADD/DROP/FIX + 근거 사실)로
        구조화 렌더된다(파싱 실패 시엔 원문 폴백이므로 이 단언이 구조화 경로를 증명). """
    _goto_qa(page)
    page.get_by_text("데이터 기반 제언", exact=True).click()   # 라디오 라벨(실제 input 은 숨김)
    _ask(page, "2026 설문 설계 제언")

    for frag in ["KEEP · 유지", "ADD · 신설", "DROP · 축소", "FIX · 설계 교정"]:
        expect(page.get_by_text(frag, exact=True).first).to_be_visible(timeout=20000)
    expect(page.get_by_text(re.compile("근거 사실 보기"))).to_be_visible(timeout=15000)
