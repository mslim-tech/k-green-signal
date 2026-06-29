# tests/test_integrate_worksheet.py
# -----------------------------------------------------------------------------
# integrate_oldyears 의 mapping_review.csv 워크시트 교정 로더 결정적 단위 검증.
#   - proposed 가 채워진 행만 (year, current_std_id, subsection 접두사) 로 교정
#   - 접두사 매칭(워크시트 subsection 은 잘려 있음) · 가장 긴 접두사 우선
#   - build_rows 가 워크시트대로 std_id 를 바꾸고 새 std_id 항목을 합성
# LLM 불필요(저장 매핑 없이 함수 직접 호출).
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag import integrate_oldyears as I


def test_apply_worksheet_prefix_and_longest():
    overrides = [
        {"year": "2016", "current_std_id": "그린카드_포인트희망품목",
         "prefix": "그린카드 사용자 대상 에코머니 포인트 기부",  # 잘린 접두사
         "proposed": "그린카드_포인트기부의향"},
        {"year": "2016", "current_std_id": "그린카드_포인트희망품목",
         "prefix": "그린카드 사용자",  # 더 짧은 접두사(겹침)
         "proposed": "WRONG"},
    ]
    # 실제 레코드 subsection 은 접두사보다 길다 → startswith 매칭
    full = "그린카드 사용자 대상 에코머니 포인트 기부 의향(전체 기준)"
    sid = I._apply_worksheet("2016", full, "그린카드_포인트희망품목", overrides)
    assert sid == "그린카드_포인트기부의향"   # 가장 긴 접두사 우선


def test_apply_worksheet_guards():
    ov = [{"year": "2016", "current_std_id": "A", "prefix": "문항", "proposed": "B"}]
    # 연도 불일치 → 교정 안 함
    assert I._apply_worksheet("2017", "문항 텍스트", "A", ov) == "A"
    # 현재 std_id 불일치 → 교정 안 함
    assert I._apply_worksheet("2016", "문항 텍스트", "Z", ov) == "Z"
    # 접두사 불일치 → 교정 안 함
    assert I._apply_worksheet("2016", "다른 문항", "A", ov) == "A"


def test_build_rows_applies_worksheet(monkeypatch):
    # 워크시트 로더를 가짜 override 로 대체(파일·실제 outputs 불사용)
    monkeypatch.setattr(I, "load_worksheet_overrides", lambda: [
        {"year": "2016", "current_std_id": "그린카드_포인트희망품목",
         "prefix": "그린카드 사용자 대상 에코머니 포인트 기부",
         "proposed": "그린카드_포인트기부의향"}])
    records = [{
        "source": "s.pdf", "year": "2016",
        "subsection": "그린카드 사용자 대상 에코머니 포인트 기부 의향(전체 기준)",
        "response_items": [{"label": "있음", "value": "88.6"}],
    }]
    qmap = {("s.pdf", records[0]["subsection"]): "그린카드_포인트희망품목"}
    dictionary = {"그린카드_포인트희망품목":
                  {"std_id": "그린카드_포인트희망품목", "std_label": "희망 품목", "category": "그린카드"}}
    rows = I.build_rows(records, qmap, dictionary)
    assert rows and rows[0]["std_id"] == "그린카드_포인트기부의향"
    # 새 std_id 항목 합성(카테고리는 base 승계)
    assert dictionary["그린카드_포인트기부의향"]["category"] == "그린카드"


def test_build_rows_multiword_split_label(monkeypatch):
    # 복수응답 분리 시 라벨에 식별 꼬리표가 붙는지
    monkeypatch.setattr(I, "load_worksheet_overrides", lambda: [
        {"year": "2022", "current_std_id": "친환경제품_판단기준",
         "prefix": "친환경제품(녹색제품 포함) 판단 기준(1+2+3순위 복수응답)",
         "proposed": "친환경제품_판단기준_복수응답"}])
    records = [{
        "source": "s.pdf", "year": "2022",
        "subsection": "친환경제품(녹색제품 포함) 판단 기준(1+2+3순위 복수응답)",
        "response_items": [{"label": "환경표지 확인", "value": "79.1"}],
    }]
    qmap = {("s.pdf", records[0]["subsection"]): "친환경제품_판단기준"}
    dictionary = {"친환경제품_판단기준":
                  {"std_id": "친환경제품_판단기준", "std_label": "판단 기준", "category": "친환경제품"}}
    rows = I.build_rows(records, qmap, dictionary)
    assert rows[0]["std_id"] == "친환경제품_판단기준_복수응답"
    assert "복수응답" in dictionary["친환경제품_판단기준_복수응답"]["std_label"]
