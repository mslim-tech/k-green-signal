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
    expect(page.get_by_text(re.compile("실시간 신호등")).first).to_be_visible(timeout=30000)

    # 검수는 🛠 데이터 준비 모드의 3단계.
    page.get_by_role("button", name=re.compile("데이터 준비")).click()
    page.get_by_role("button", name=re.compile(r"3\. 🔍 검수")).click()

    # 빈값 안내(아직 남은 빈칸 경고 OR 모두 처리됨 안내) 중 하나는 보여야 한다.
    guidance = page.get_by_text(
        re.compile("값이 비어 검수가 필요한 행|값 없는 행이 모두 검수 처리")
    )
    expect(guidance.first).to_be_visible(timeout=15000)
    # '값 없는 행만 보기' 필터(체크박스) 존재 — Streamlit 실제 input 은 시각상 숨김이라 attached 로 확인
    expect(page.get_by_role("checkbox", name=re.compile("값 없는 행만 보기"))).to_be_attached(timeout=15000)


def test_review_sequential_confirm_and_page_preview(page: Page, base_url: str):
    """ 순차 검수 모드: 원문 페이지 블록(이미지 또는 폴백 문구)이 보이고, 안전 기본값
        '원래 값 맞음'으로 저장하면 검수 완료 수가 1 늘어난다(저장 후 자동 다음 행).
        (검수 완료 수로 단언하는 이유: st.dataframe 은 canvas 렌더라 ✅ 셀을 DOM 으로
         단언할 수 없다 — 같은 reviewed_keys 가 캡션 카운트를 구동한다.) """
    page.goto("/")
    expect(page.get_by_text(re.compile("실시간 신호등")).first).to_be_visible(timeout=30000)
    page.get_by_role("button", name=re.compile("데이터 준비")).click()
    page.get_by_role("button", name=re.compile(r"3\. 🔍 검수")).click()

    # 검수 완료 수(기준값) 파싱
    cap = page.get_by_text(re.compile(r"검수 완료 \d+행"))
    expect(cap).to_be_visible(timeout=15000)
    n0 = int(re.search(r"검수 완료 (\d+)행", cap.inner_text()).group(1))

    # 순차 검수 모드 켜기(토글 — 실제 input 은 숨김이라 라벨 텍스트로 클릭)
    page.get_by_text(re.compile("순차 검수 모드")).click()
    expect(page.get_by_text(re.compile(r"미검수 \d+건 남음"))).to_be_visible(timeout=15000)

    # 원문 페이지 블록: 이미지(로컬엔 PDF 있음) 또는 폴백 문구(클론/CI 엔 PDF 없음)
    expect(page.get_by_text(re.compile("원문 페이지 보기"))).to_be_visible(timeout=15000)
    preview = page.get_by_test_id("stImage").or_(
        page.get_by_text(re.compile("원문 PDF 가 data/ 에 없어|출처 페이지 정보가 없어")))
    expect(preview.first).to_be_visible(timeout=20000)

    # 안전 기본값: 라디오 첫 옵션 = '원래 값 맞음'이 기본 선택돼 있다.
    expect(page.get_by_text("원래 값 맞음", exact=True).first).to_be_visible(timeout=15000)
    expect(page.locator("input[type='radio']").first).to_be_checked(timeout=15000)

    # 저장 → 검수 완료 수 +1 (저장된 행은 미검수 목록에서 빠지고 카운트가 오른다)
    page.get_by_role("button", name=re.compile("저장")).click()
    expect(page.get_by_text(re.compile(rf"검수 완료 {n0 + 1}행"))).to_be_visible(timeout=20000)
