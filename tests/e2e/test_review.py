# tests/e2e/test_review.py
# -----------------------------------------------------------------------------
# 검수(3단계): 값이 비어 검수가 필요한 행을 사용자가 직접 찾지 않도록
# 배너 + '값 없는 행만 보기' 필터가 안내되는지 확인(D3).
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def test_review_blank_value_guidance(page: Page, base_url: str):
    page.goto("/")
    expect(page.get_by_text(re.compile("신호등"))).to_be_visible(timeout=30000)

    page.get_by_role("button", name=re.compile(r"3\. 🔍 검수")).click()

    # 값이 비어 검수가 필요한 행 안내 배너
    expect(page.get_by_text(re.compile("값이 비어 검수가 필요한 행"))).to_be_visible(timeout=15000)
    # '값 없는 행만 보기' 필터(체크박스) 존재 — Streamlit 실제 input 은 시각상 숨김이라 attached 로 확인
    expect(page.get_by_role("checkbox", name=re.compile("값 없는 행만 보기"))).to_be_attached(timeout=15000)
