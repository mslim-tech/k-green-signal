# tests/e2e/test_review.py
# -----------------------------------------------------------------------------
# 검수(3단계): 값이 비어 검수가 필요한 행을 사용자가 직접 찾지 않도록
# 배너/안내 + '값 없는 행만 보기' 필터가 동작하는지 확인(D3).
#   - 아직 손대지 않은 빈칸이 있으면 "값이 비어 검수가 필요한 행" 경고,
#   - 모두 처리(빈 값 확인/제외)됐으면 "모두 검수 처리되었습니다" 안내.
#   둘 중 하나는 항상 보여야 하고, '값 없는 행만 보기' 필터는 항상 있어야 한다.
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def test_review_blank_value_guidance(page: Page, base_url: str):
    page.goto("/")
    expect(page.get_by_text(re.compile("신호등"))).to_be_visible(timeout=30000)

    page.get_by_role("button", name=re.compile(r"3\. 🔍 검수")).click()

    # 빈값 안내(아직 남은 빈칸 경고 OR 모두 처리됨 안내) 중 하나는 보여야 한다.
    guidance = page.get_by_text(
        re.compile("값이 비어 검수가 필요한 행|값 없는 행이 모두 검수 처리")
    )
    expect(guidance.first).to_be_visible(timeout=15000)
    # '값 없는 행만 보기' 필터(체크박스) 존재 — Streamlit 실제 input 은 시각상 숨김이라 attached 로 확인
    expect(page.get_by_role("checkbox", name=re.compile("값 없는 행만 보기"))).to_be_attached(timeout=15000)
