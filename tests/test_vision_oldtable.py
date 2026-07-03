# tests/test_vision_oldtable.py
# -----------------------------------------------------------------------------
# 옛 표 비전 추출의 순수 변환부 검증 (LLM·PDF 불필요·결정적).
#   - _is_agg: 집계/머리글 라벨 제외 판정
#   - to_records: 비전 다년 결과 → 연도별 레코드(집계행·빈값 제외, 연도는 행에서)
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.ingest import extract_vision_oldtable as ov


def test_is_agg():
    # 항상 집계·머리글(응답 보기 아님)
    for bad in ["[TOP3]", "[TOP2]", "소계", "합계", "사례수", "구분", ""]:
        assert ov._is_agg(bad) is True
    # 실제 보기 + '인지/비인지'(이진형에선 보존 → _is_agg 단독으론 False, 척도형 처리는 _DERIVED)
    for good in ["잘 알고 있다", "생활용품", "친환경 농산물", "인지", "비인지"]:
        assert ov._is_agg(good) is False


def test_to_records_multiyear_filters_and_year_from_row():
    result = {
        "question_summary": "인증마크 인지도",
        "unit": "%", "multi_response": False,
        "years": [
            {"year": 2021, "base_n": 1000, "items": [
                {"label": "잘 알고 있다", "value": 4.0},
                {"label": "[TOP3]", "value": 67.9},          # 집계 → 제외
                {"label": "사례수", "value": 1000},           # 머리글 → 제외
            ]},
            {"year": 2022, "base_n": 1000, "items": [
                {"label": "잘 알고 있다", "value": 3.9},
                {"label": "조금 알고 있다", "value": 27.2},
                {"label": "본 적은 있다", "value": None},      # 빈값 → 제외
            ]},
        ],
    }
    recs = ov.to_records(result, "2022년 ....pdf", 29, "인증마크 인지도")
    assert [r["year"] for r in recs] == [2021, 2022]          # 연도는 행에서
    # 2021: 집계/머리글 빠지고 실제 보기 1개만
    r21 = recs[0]
    assert [it["label"] for it in r21["response_items"]] == ["잘 알고 있다"]
    assert r21["base_n"] == 1000 and r21["unit"] == "%"
    assert r21["source"] == "2022년 ....pdf" and r21["page_start"] == 29
    # 2022: 빈값(None) 보기 제외 → 2개
    assert [it["label"] for it in recs[1]["response_items"]] == ["잘 알고 있다", "조금 알고 있다"]


def test_clean_items_drops_scale_derived_and_duplicates():
    # 척도형: 4점 보기 + 파생 집계('인지'=TOP3, '인지'=TOP2 중복) → 척도 4개만 남는다
    items = [
        {"label": "잘 알고 있다", "value": 4.5},
        {"label": "조금 알고 있다", "value": 21.3},
        {"label": "본 적은 있다", "value": 32.0},
        {"label": "전혀 모른다/처음 듣다", "value": 42.2},
        {"label": "인지", "value": 57.8},     # 파생 집계(TOP3) → 제외
        {"label": "인지", "value": 25.8},     # 중복/파생 → 제외
    ]
    out = ov._clean_items(items)
    assert [it["label"] for it in out] == [
        "잘 알고 있다", "조금 알고 있다", "본 적은 있다", "전혀 모른다/처음 듣다"]


def test_clean_items_keeps_binary_when_no_scale():
    # 척도 보기가 없으면 '인지/비인지' 이진형은 보존한다
    items = [{"label": "인지", "value": 51.7}, {"label": "비인지", "value": 48.3}]
    out = ov._clean_items(items)
    assert [it["label"] for it in out] == ["인지", "비인지"]


def test_to_records_drops_year_with_no_items():
    result = {"question_summary": "q", "unit": "%", "multi_response": False,
              "years": [{"year": 2020, "base_n": None, "items": [
                  {"label": "[TOP3]", "value": 50.0}]}]}   # 집계뿐 → 레코드 없음
    assert ov.to_records(result, "s.pdf", 1, "t") == []
