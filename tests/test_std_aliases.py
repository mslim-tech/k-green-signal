# tests/test_std_aliases.py
# -----------------------------------------------------------------------------
# 문항 표준화 별칭(std_aliases)의 결정적 단위 검증 (LLM 불필요).
#   - std_id 통합(#1·#2), canonical std_label, 응답라벨 정렬(#2)
#   - #2가 통합 후 같은 (std_id, 라벨)로 시계열 연결되는지 (signals 까지)
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag import std_aliases, signals


def test_std_id_merge_and_label_canon():
    rows = [
        {"std_id": "환경표지_구매이유", "std_label": "환경표지 인증제품 구매 이유",
         "std_response_label": "나와 가족의 건강과 안전", "year": "2023", "value": "50.1", "unit": "%"},
        {"std_id": "환경표지_우선구매이유", "std_label": "환경표지 인증제품 우선 구매 이유",
         "std_response_label": "나와 가족의 건강과 안전에 도움", "year": "2024", "value": "71.1", "unit": "%"},
    ]
    out = std_aliases.apply_aliases(rows)
    # #1: std_id 통합 + canonical 라벨
    assert all(r["std_id"] == "환경표지_우선구매이유" for r in out)
    assert all(r["std_label"] == "환경표지 인증제품 우선 구매 이유" for r in out)
    # #1은 응답라벨 정렬 안 함(단일↔복수라 가짜추세 방지) → 원문 유지
    assert out[0]["std_response_label"] == "나와 가족의 건강과 안전"


def test_response_label_alias_connects_series():
    rows = [
        {"std_id": "환경표지_재구매의향", "std_label": "환경표지 인증제품 재구매 의향",
         "std_response_label": "의향 있음", "year": "2023", "value": "96.0", "unit": "%",
         "category": "구매 의향"},
        {"std_id": "환경표지_우선구매의향", "std_label": "환경표지 인증제품 우선 구매 의향",
         "std_response_label": "구매 의향 있음", "year": "2025", "value": "93.8", "unit": "%",
         "category": "구매 의향"},
    ]
    out = std_aliases.apply_aliases(rows)
    # #2: 둘 다 같은 std_id + 같은 응답라벨로 통일
    assert {r["std_id"] for r in out} == {"환경표지_우선구매의향"}
    assert {r["std_response_label"] for r in out} == {"구매 의향 있음"}
    # → signals 에서 한 시계열로 연결(2023→2025)
    inds = signals.compute_signals(out)
    assert len(inds) == 1
    s = inds[0].series[0]
    assert [p.year for p in s.points] == [2023, 2025]
    assert s.latest.value == 93.8


def test_term_normalize_carbon_to_epd():
    # 탄소성적표지·탄소발자국 → 환경성적표지 (std_id·std_label 둘 다)
    rows = [
        {"std_id": "탄소성적표지_인지도", "std_label": "탄소성적표지 인지도",
         "std_response_label": "인지", "year": "2015", "value": "50.0", "unit": "%"},
        {"std_id": "탄소발자국_우선구매이유", "std_label": "탄소발자국 우선 구매 이유",
         "std_response_label": "이유", "year": "2018", "value": "60.0", "unit": "%"},
    ]
    out = std_aliases.apply_aliases(rows)
    assert {r["std_id"] for r in out} == {"환경성적표지_인지도", "환경성적표지_우선구매이유"}
    assert all("환경성적표지" in r["std_label"] for r in out)
    # 저탄소제품은 영향 없음
    assert std_aliases._normalize_terms("저탄소제품_인지도") == "저탄소제품_인지도"


def test_carbon_footprint_awareness_is_exempt():
    # 2017 탄소발자국(구 탄소성적표지) 마크 인지도는 환경성적표지 로고 인지도와
    # 별개 표 → 용어정규화 제외(사용자 확정). 이 id 만 환경성적표지로 합쳐지지 않는다.
    rows = [{"std_id": "탄소발자국_인지도", "std_label": "탄소발자국 인지도",
             "std_response_label": "비인지", "year": "2017", "value": "22.4", "unit": "%"}]
    out = std_aliases.apply_aliases(rows)
    assert out[0]["std_id"] == "탄소발자국_인지도"
    assert "탄소발자국" in out[0]["std_label"]


def test_derive_aggregate_from_mark_renewal():
    # 2017 환경표지 인지도는 로고 리뉴얼로 '신/구마크' 형식 → 척도 보기가 없다.
    # 대체 보기집합(신/구마크)으로 '인지'(=알아본 합)를 도출해 시계열에 연결한다.
    rows = [
        {"std_id": "환경표지_인지도", "std_response_label": "신마크/구마크 모두 인지",
         "year": "2017", "value": "61.7", "unit": "%"},
        {"std_id": "환경표지_인지도", "std_response_label": "신마크만 인지",
         "year": "2017", "value": "19.0", "unit": "%"},
        {"std_id": "환경표지_인지도", "std_response_label": "구마크만 인지",
         "year": "2017", "value": "1.9", "unit": "%"},
        {"std_id": "환경표지_인지도", "std_response_label": "신마크/구마크 모두 비인지",
         "year": "2017", "value": "17.4", "unit": "%"},
    ]
    out = std_aliases.derive_aggregates(rows)
    인지 = [r for r in out if r["std_response_label"] == "인지" and r["year"] == "2017"]
    assert len(인지) == 1 and 인지[0]["value"] == "82.6"   # 61.7+19.0+1.9


def test_derive_aggregate_epd_mark_renewal():
    # 환경성적표지도 2017 신/구마크 형식 → 같은 방식으로 인지 도출(58.5).
    rows = [
        {"std_id": "환경성적표지_인지도", "std_response_label": "신마크/구마크 모두 인지",
         "year": "2017", "value": "38.8", "unit": "%"},
        {"std_id": "환경성적표지_인지도", "std_response_label": "신마크만 인지",
         "year": "2017", "value": "18.6", "unit": "%"},
        {"std_id": "환경성적표지_인지도", "std_response_label": "구마크만 인지",
         "year": "2017", "value": "1.1", "unit": "%"},
        {"std_id": "환경성적표지_인지도", "std_response_label": "신마크/구마크 모두 비인지",
         "year": "2017", "value": "41.5", "unit": "%"},
    ]
    out = std_aliases.derive_aggregates(rows)
    인지 = [r for r in out if r["std_response_label"] == "인지" and r["year"] == "2017"]
    assert len(인지) == 1 and 인지[0]["value"] == "58.5"   # 38.8+18.6+1.1


def test_response_canon_connects_eras():
    rows = [
        {"std_id": "환경문제_관심도", "std_response_label": "[관심]",
         "year": "2020", "value": "90.8", "unit": "%"},
        {"std_id": "환경문제_관심도", "std_response_label": "관심 있음(1+2)",
         "year": "2024", "value": "96.4", "unit": "%"},
    ]
    out = std_aliases.apply_aliases(rows)
    assert {r["std_response_label"] for r in out} == {"관심 있음"}


def test_derive_aggregates_sums_components():
    rows = [
        {"std_id": "환경표지_인지도", "std_response_label": "잘 알고 있다",
         "year": "2018", "value": "10.0", "unit": "%"},
        {"std_id": "환경표지_인지도", "std_response_label": "조금 알고 있다",
         "year": "2018", "value": "30.0", "unit": "%"},
        {"std_id": "환경표지_인지도", "std_response_label": "본 적은 있다",
         "year": "2018", "value": "43.9", "unit": "%"},
    ]
    out = std_aliases.derive_aggregates(rows)
    derived = [r for r in out if r["std_response_label"] == "인지"]
    assert len(derived) == 1
    assert float(derived[0]["value"]) == 83.9 and derived[0]["year"] == "2018"


def test_derive_skips_when_aggregate_exists():
    rows = [
        {"std_id": "환경표지_인지도", "std_response_label": "인지",
         "year": "2023", "value": "90.7", "unit": "%"},
        {"std_id": "환경표지_인지도", "std_response_label": "잘 알고 있다",
         "year": "2023", "value": "20.0", "unit": "%"},
    ]
    # 이미 '인지'가 있으면(최근) 도출하지 않는다 → 중복 없음
    assert len([r for r in std_aliases.derive_aggregates(rows)
                if r["std_response_label"] == "인지"]) == 1


def test_backfill_green_product_awareness_connects_eras():
    # #3 녹색제품_인지도('23~): '19~22는 환경표지 마크 인지도로 연결(2023 보고서 그림 2-18).
    #   '19~20 = '잘 알고 있다'(top1), '21~ = '잘+조금'(top2). 그림 2-18과 값 일치.
    rows = [
        {"std_id": "환경표지_인지도", "std_response_label": "잘 알고 있다",
         "year": "2019", "value": "16.5", "unit": "%", "source": "s", "page_start": "96"},
        {"std_id": "환경표지_인지도", "std_response_label": "조금 알고 있다",
         "year": "2019", "value": "41.7", "unit": "%", "source": "s", "page_start": "96"},
        {"std_id": "환경표지_인지도", "std_response_label": "잘 알고 있다",
         "year": "2021", "value": "7.5", "unit": "%", "source": "s", "page_start": "96"},
        {"std_id": "환경표지_인지도", "std_response_label": "조금 알고 있다",
         "year": "2021", "value": "33.4", "unit": "%", "source": "s", "page_start": "96"},
        # 대상에 이미 있는 '23은 백필하지 않는다(덮어쓰기 금지).
        {"std_id": "녹색제품_인지도", "std_label": "녹색제품 인지도",
         "std_response_label": "인지(잘 알고 있다+조금 알고 있다)",
         "year": "2023", "value": "51.7", "unit": "%"},
    ]
    out = std_aliases.backfill_series(rows)
    green = {r["year"]: r for r in out if r["std_id"] == "녹색제품_인지도"
             and r["std_response_label"] == "인지(잘 알고 있다+조금 알고 있다)"}
    assert green["2019"]["value"] == "16.5"       # top1: 잘만
    assert green["2021"]["value"] == "40.9"       # top2: 7.5+33.4
    assert green["2023"]["value"] == "51.7"       # 원래 있던 것 그대로(중복 없음)
    assert len([r for r in out if r["std_id"] == "녹색제품_인지도"
                and r["year"] == "2023"]) == 1
    # 값 출처는 과거 문항 그대로 계승, 연결 근거는 warning 에 남는다.
    assert green["2019"]["source"] == "s"
    assert "그림 2-18" in green["2019"]["warning"]
    # 필요한 보기가 없는 해(2020: 조금 없음... 여기선 2020 자체가 없음)는 만들지 않는다.
    assert "2020" not in green and "2022" not in green


def test_backfill_skips_when_component_missing():
    # 보기가 다 없으면 백필하지 않는다(추측 금지).
    rows = [{"std_id": "환경표지_인지도", "std_response_label": "잘 알고 있다",
             "year": "2021", "value": "7.5", "unit": "%"}]   # '조금 알고 있다' 없음
    out = std_aliases.backfill_series(rows)
    assert not [r for r in out if r["std_id"] == "녹색제품_인지도" and r["year"] == "2021"]


def test_untouched_rows_passthrough():
    rows = [{"std_id": "녹색제품_인지도", "std_label": "녹색제품 인지도",
             "std_response_label": "인지", "year": "2024", "value": "55.0", "unit": "%"}]
    out = std_aliases.apply_aliases(rows)
    assert out[0]["std_id"] == "녹색제품_인지도"
    assert out[0]["std_label"] == "녹색제품 인지도"
