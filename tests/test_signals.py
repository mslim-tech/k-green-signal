# tests/test_signals.py
# -----------------------------------------------------------------------------
# 실시간 신호등 추세 계산의 결정적 단위 검증 (LLM·Streamlit 불필요).
#   - YoY(%p) 계산, 신호 임계값(up/flat/down), 비%·단일연도 처리
#   - 변화 큰 순 정렬, 카테고리/집계 헬퍼
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag import signals


def _row(std_id, year, label, value, unit="%", category="인지",
         std_label=None, page="33", source="2025년 인지도조사.pdf"):
    return {
        "std_id": std_id, "year": str(year), "std_response_label": label,
        "value": str(value), "unit": unit, "category": category,
        "std_label": std_label or std_id, "question_summary": "요약",
        "page_start": page, "page_end": page, "source": source,
    }


def test_yoy_and_signal_up():
    rows = [_row("a", 2023, "인지", 51.7), _row("a", 2024, "인지", 55.0),
            _row("a", 2025, "인지", 58.0)]
    inds = signals.compute_signals(rows)
    assert len(inds) == 1
    s = inds[0].series[0]
    assert [p.year for p in s.points] == [2023, 2024, 2025]
    assert s.latest.value == 58.0
    assert s.delta == 3.0          # 최신 두 시점(2024→2025)
    assert s.signal() == "up"


def test_signal_down_and_flat_by_threshold():
    down = [_row("d", 2024, "x", 50.0), _row("d", 2025, "x", 45.0)]
    flat = [_row("f", 2024, "y", 50.0), _row("f", 2025, "y", 51.5)]
    assert signals.compute_signals(down)[0].series[0].signal() == "down"
    assert signals.compute_signals(flat)[0].series[0].signal() == "flat"
    # 임계값을 낮추면 보합이 상승으로 바뀐다
    assert signals.compute_signals(flat)[0].series[0].signal(threshold_pp=1.0) == "up"


def test_non_percent_unit_has_no_signal():
    rows = [_row("n", 2024, "점수", 3.1, unit="점"),
            _row("n", 2025, "점수", 3.9, unit="점")]
    s = signals.compute_signals(rows)[0].series[0]
    assert s.delta == 0.8          # 추세(변화)는 계산되지만
    assert s.signal() is None      # %가 아니라 신호는 없음


def test_single_year_dropped():
    rows = [_row("one", 2025, "인지", 60.0)]   # 한 연도뿐 → 시계열 안 됨
    assert signals.compute_signals(rows) == []


def test_blank_and_nonnumeric_values_skipped():
    rows = [_row("b", 2023, "인지", ""), _row("b", 2024, "인지", "n/a"),
            _row("b", 2024, "인지", 40.0), _row("b", 2025, "인지", 44.0)]
    inds = signals.compute_signals(rows)
    # 빈/비숫자 점은 빠지고 2024·2025 두 점만 남는다
    s = inds[0].series[0]
    assert [p.year for p in s.points] == [2024, 2025]
    assert s.delta == 4.0


def test_sorted_by_abs_delta():
    rows = [
        _row("small", 2024, "x", 50.0), _row("small", 2025, "x", 51.0),   # Δ +1
        _row("big", 2024, "y", 50.0), _row("big", 2025, "y", 60.0),       # Δ +10
    ]
    inds = signals.compute_signals(rows)
    assert [i.std_id for i in inds] == ["big", "small"]   # 변화 큰 게 먼저


def test_min_coverage_filters_to_continuous():
    rows = [
        # 3개년 모두(연속 추적 가능)
        _row("full", 2023, "인지", 50.0), _row("full", 2024, "인지", 53.0),
        _row("full", 2025, "인지", 56.0),
        # 2개년만(불연속)
        _row("partial", 2024, "x", 50.0), _row("partial", 2025, "x", 55.0),
    ]
    assert signals.dataset_years(rows) == [2023, 2024, 2025]
    # 기본(2개년 이상): 둘 다
    assert {i.std_id for i in signals.compute_signals(rows)} == {"full", "partial"}
    # 연속 추적(3개년 모두): full 만
    cont = signals.compute_signals(rows, min_coverage=3)
    assert {i.std_id for i in cont} == {"full"}


def test_summarize_and_categories():
    rows = [
        _row("a", 2024, "x", 50.0, category="인지"),
        _row("a", 2025, "x", 56.0, category="인지"),       # up
        _row("b", 2024, "y", 50.0, category="정책"),
        _row("b", 2025, "y", 44.0, category="정책"),       # down
        _row("c", 2024, "z", 50.0, category="정책"),
        _row("c", 2025, "z", 51.0, category="정책"),       # flat
    ]
    inds = signals.compute_signals(rows)
    assert signals.summarize(inds) == {"up": 1, "flat": 1, "down": 1}
    assert signals.categories(inds) == ["정책", "인지"]   # 정책 2 > 인지 1
