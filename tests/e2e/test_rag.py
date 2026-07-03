# tests/e2e/test_rag.py
# -----------------------------------------------------------------------------
# 💬 질의(Q&A) 모드: 질문하면 출처 인용([출처:)이 포함된 답변과 처리 시간이 보인다.
# (RAG_FAKE_LLM=1 → 답변은 결정적 스텁이지만 인용 형식·타이밍 UI 를 검증.)
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def test_rag_answer_has_citation_and_timing(page: Page, base_url: str):
    page.goto("/")
    expect(page.get_by_text(re.compile("실시간 신호등")).first).to_be_visible(timeout=30000)

    # 💬 질의 모드로 이동 — 전환 완료를 센티넬로 기다린다(랜딩 신호등 화면의
    # '🔎 질문/키워드' 입력이 '질문' 정규식에 먼저 걸리는 경합 방지).
    page.get_by_role("button", name=re.compile(r"질의\(Q&A\)")).click()
    expect(page.locator("[data-testid='mode-status']")).to_have_text("qa", timeout=15000)
    box = page.get_by_role("textbox", name="질문", exact=True)
    expect(box).to_be_visible(timeout=15000)
    box.fill("녹색제품 인지율은?")
    box.press("Enter")

    # 근거 인용 표기 + 처리 시간(왜 느린지) 표시 — 스텁 답변의 인용 1건을 단언
    expect(page.get_by_text(re.compile(r"\[출처:")).first).to_be_visible(timeout=20000)
    expect(page.get_by_text(re.compile("처리 시간"))).to_be_visible(timeout=20000)
