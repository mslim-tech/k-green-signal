# tests/e2e/test_ingest.py
# -----------------------------------------------------------------------------
# 인제스트 단계: '전체 실행' 시 진행 UI(진행바/단계목록)가 뜨고, '취소'로 멈추는지.
# (E2E 서버는 RAG_FAKE_LLM=1 → 추출 단계가 무료/빠름. 취소로 표준화 진입 전 종료.)
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def test_ingest_run_then_cancel(page: Page, base_url: str):
    page.goto("/")
    expect(page.get_by_text(re.compile("신호등"))).to_be_visible(timeout=30000)

    # 2단계(인제스트)로 이동
    page.get_by_role("button", name=re.compile(r"2\. ⚙️ 인제스트")).click()
    expect(page.get_by_role("button", name=re.compile("전체 실행"))).to_be_visible(timeout=15000)

    # 실행 → 진행 표시(진행바 텍스트)가 떠야 한다
    page.get_by_role("button", name=re.compile("전체 실행")).click()
    expect(page.get_by_text(re.compile("인제스트:"))).to_be_visible(timeout=15000)

    # 곧바로 취소 → 취소 메시지
    page.get_by_role("button", name=re.compile("취소")).click()
    expect(page.get_by_text(re.compile("취소되었습니다"))).to_be_visible(timeout=15000)
