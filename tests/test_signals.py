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


def test_gap_in_latest_two_points_has_no_signal():
    # 최신 두 점이 인접 연도가 아니면(예: 2017→2023) 6년치를 한 스텝처럼 보는
    # '가짜 점프'라 신호를 매기지 않는다(추세선/Δ 계산은 그대로).
    rows = [_row("g", 2017, "x", 10.0), _row("g", 2023, "x", 80.0)]
    s = signals.compute_signals(rows)[0].series[0]
    assert s.delta == 70.0          # 변화는 계산되지만
    assert s.is_yoy is False
    assert s.signal() is None       # 인접 연도가 아니라 신호 없음
    # 인접 연도면 정상 신호
    adj = [_row("h", 2024, "x", 10.0), _row("h", 2025, "x", 80.0)]
    sa = signals.compute_signals(adj)[0].series[0]
    assert sa.is_yoy is True and sa.signal() == "up"


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


# --- 의사결정 프레이밍(집계 제외·척도변경 보류·거울상 중복제거) 회귀 가드 --------------
# summarize/signaled_movers 의미가 '실제 변화만 계수'로 바뀐 것을 고정한다.

def test_aggregation_label_excluded_from_movers_and_summary():
    rows = [_row("m", 2024, "인지", 40.0), _row("m", 2025, "인지", 50.0),
            _row("m", 2024, "기타", 10.0), _row("m", 2025, "기타", 30.0)]
    inds = signals.compute_signals(rows, caveated_ids=set())
    agg = next(s for s in inds[0].series if s.label == "기타")
    assert agg.is_aggregate and agg.signal() is None       # 집계 라벨은 신호 없음
    assert agg.block_reason == "집계·비응답 항목"
    movers = signals.signaled_movers(inds)
    assert [s.label for _, s in movers] == ["인지"]         # 헤드라인에서 제외
    assert signals.summarize(inds) == {"up": 1, "flat": 0, "down": 0}


def test_binary_mirror_counted_once():
    # 이진 상보(합~100) 문항은 대표 1개(최신값 큰 쪽)만 계수 — 거울상 중복 방지.
    rows = [_row("b", 2024, "인지", 40.0), _row("b", 2025, "인지", 55.0),
            _row("b", 2024, "비인지", 60.0), _row("b", 2025, "비인지", 45.0)]
    inds = signals.compute_signals(rows, caveated_ids=set())
    assert inds[0].is_binary_mirror
    movers = signals.signaled_movers(inds)
    assert len(movers) == 1 and movers[0][1].label == "인지"
    assert signals.summarize(inds) == {"up": 1, "flat": 0, "down": 0}


def test_caveated_scale_break_blocks_signal():
    # 척도변경 문항의 '23→'24 전이는 신호 보류(⚠️ 해석 유의) — 실제 추세로 오독 방지.
    rows = [_row("c", 2023, "인지", 20.0), _row("c", 2024, "인지", 60.0)]
    inds = signals.compute_signals(rows, caveated_ids={"c"})
    s = inds[0].series[0]
    assert s.caveat_break and s.signal() is None
    assert s.block_reason == "척도 변경 구간"
    assert signals.summarize(inds) == {"up": 0, "flat": 0, "down": 0}
    assert [(i.std_id, sr.label) for i, sr in signals.caveat_breaks(inds)] == [("c", "인지")]
    # 같은 데이터라도 척도변경 문항이 아니면 정상 신호 — caveat 게이트가 원인임을 고정.
    s2 = signals.compute_signals(rows, caveated_ids=set())[0].series[0]
    assert s2.signal() == "up"


def test_max_abs_delta_ignores_aggregate_and_caveat():
    # 지표 정렬 기준(max_abs_delta)도 집계·척도변경 시계열을 무시한다.
    rows = [_row("x", 2024, "인지", 50.0), _row("x", 2025, "인지", 52.0),      # Δ2
            _row("x", 2024, "기타", 10.0), _row("x", 2025, "기타", 40.0)]      # Δ30(집계)
    ind = signals.compute_signals(rows, caveated_ids=set())[0]
    assert ind.max_abs_delta == 2.0


# --- big_change_years: 외부 맥락 검색 대상 '변화 폭 큰 해' 탐지 -----------------

def test_big_change_years_detects_sorts_and_dedups():
    # k: 2021→2022 +13, 2023→2024 -20 (그 외 인접 변화는 임계 미만)
    k = [_row("k", 2020, "인지", 50.0), _row("k", 2021, "인지", 52.0),
         _row("k", 2022, "인지", 65.0), _row("k", 2023, "인지", 64.0),
         _row("k", 2024, "인지", 44.0)]
    # k2: 2021→2022 +25 → 같은 2022 해에서 |Δ| 더 큼 → 2022 대표는 k2 가 이긴다(연도 1건)
    k2 = [_row("k2", 2021, "관심", 5.0), _row("k2", 2022, "관심", 30.0)]
    inds = signals.compute_signals(k + k2, caveated_ids=set())
    changes = signals.big_change_years(inds, threshold_pp=5.0, caveated_ids=set())
    assert [c.year for c in changes] == [2022, 2024]        # |25| > |20|
    assert [c.delta for c in changes] == [25.0, -20.0]
    assert changes[0].indicator_label == "k2" and changes[0].prev_year == 2021


def test_big_change_years_excludes_gap_and_aggregate():
    # 비인접(가짜 점프)·집계 라벨은 검색 대상에서 제외된다.
    gap = [_row("g", 2017, "인지", 10.0), _row("g", 2023, "인지", 80.0)]       # 간격 6
    agg = [_row("a", 2024, "기타", 10.0), _row("a", 2025, "기타", 40.0)]       # 집계 Δ30
    inds = signals.compute_signals(gap + agg, caveated_ids=set())
    assert signals.big_change_years(inds, threshold_pp=5.0, caveated_ids=set()) == []


def test_big_change_years_threshold_and_scale_break():
    # 척도변경 문항의 '23→'24 큰 변화는 측정방식 변화일 수 있어 제외(신호 규칙과 일관).
    rows = [_row("c", 2022, "인지", 20.0), _row("c", 2023, "인지", 22.0),
            _row("c", 2024, "인지", 60.0)]                                     # 2023→2024 +38
    inds = signals.compute_signals(rows, caveated_ids={"c"})
    assert signals.big_change_years(inds, threshold_pp=5.0, caveated_ids={"c"}) == []
    # 척도변경 문항이 아니면 2024 가 검색 대상으로 잡힌다(임계값도 확인)
    inds2 = signals.compute_signals(rows, caveated_ids=set())
    got = signals.big_change_years(inds2, threshold_pp=5.0, caveated_ids=set())
    assert [c.year for c in got] == [2024] and got[0].delta == 38.0
    assert signals.big_change_years(inds2, threshold_pp=40.0, caveated_ids=set()) == []
