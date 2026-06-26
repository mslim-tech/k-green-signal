# tests/e2e/test_rag.py
# -----------------------------------------------------------------------------
# 통합 RAG Q&A(5단계): 질문하면 출처 인용([출처:)이 포함된 답변과 처리 시간이 보인다.
# (RAG_FAKE_LLM=1 → 답변은 결정적 스텁이지만 인용 형식·타이밍 UI 를 검증.)
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def test_rag_answer_has_citation_and_timing(page: Page, base_url: str):
    page.goto("/")
    expect(page.get_by_text(re.compile("신호등"))).to_be_visible(timeout=30000)

    # 5단계(질의)로 이동 — 인덱스가 있어 입장 가능
    page.get_by_role("button", name=re.compile(r"5\. 💬 질의")).click()
    box = page.get_by_role("textbox", name=re.compile("질문"))
    expect(box).to_be_visible(timeout=15000)
    box.fill("녹색제품 인지율은?")
    box.press("Enter")

    # 근거 인용 표기 + 처리 시간(왜 느린지) 표시
    expect(page.get_by_text(re.compile(r"\[출처:"))).to_be_visible(timeout=20000)
    expect(page.get_by_text(re.compile("처리 시간"))).to_be_visible(timeout=20000)
