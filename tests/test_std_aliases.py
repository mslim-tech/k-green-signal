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


def test_untouched_rows_passthrough():
    rows = [{"std_id": "녹색제품_인지도", "std_label": "녹색제품 인지도",
             "std_response_label": "인지", "year": "2024", "value": "55.0", "unit": "%"}]
    out = std_aliases.apply_aliases(rows)
    assert out[0]["std_id"] == "녹색제품_인지도"
    assert out[0]["std_label"] == "녹색제품 인지도"
