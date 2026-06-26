# tests/e2e/test_ingest.py
# -----------------------------------------------------------------------------
# 인제스트(2단계):
#  - 산출이 최신이면 전부 '스킵(최신)' 하고 완료 (D1 스킵 캐시)
#  - '강제 재실행' 시 실제 단계가 떠서 진행 표시 → '취소'로 멈춤
# (E2E 서버는 RAG_FAKE_LLM=1 → 추출이 빠름. 취소는 표준화 진입 전.)
# -----------------------------------------------------------------------------

import re

from playwright.sync_api import Page, expect


def _goto_ingest(page: Page):
    page.goto("/")
    expect(page.get_by_text(re.compile("신호등"))).to_be_visible(timeout=30000)
    page.get_by_role("button", name=re.compile(r"2\. ⚙️ 인제스트")).click()
    expect(page.get_by_role("button", name=re.compile("전체 실행"))).to_be_visible(timeout=15000)


def test_ingest_skips_when_fresh(page: Page, base_url: str):
    """ D1: 산출이 최신이면 LLM 없이 전부 스킵하고 완료된다. """
    _goto_ingest(page)
    page.get_by_role("button", name=re.compile("전체 실행")).click()
    # 스킵 라인은 단계마다 나오므로 .first 로(strict 위반 방지)
    expect(page.get_by_text(re.compile(r"스킵\(최신\)")).first).to_be_visible(timeout=20000)
    expect(page.get_by_text(re.compile("인제스트 완료"))).to_be_visible(timeout=20000)


def test_ingest_force_run_then_cancel(page: Page, base_url: str):
    """ '강제 재실행' 켜면 실제 단계가 떠서 진행 표시 → '취소'로 멈춘다. """
    _goto_ingest(page)
    page.get_by_text(re.compile(r"강제 재실행")).click()   # 스킵 끄기(강제)
    page.get_by_role("button", name=re.compile("전체 실행")).click()
    expect(page.get_by_text(re.compile("인제스트:"))).to_be_visible(timeout=15000)
    page.get_by_role("button", name=re.compile("취소")).click()
    expect(page.get_by_text(re.compile("취소되었습니다"))).to_be_visible(timeout=15000)
