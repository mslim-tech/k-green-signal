# rag/signals.py
# -----------------------------------------------------------------------------
# 실시간 신호등 — 연도별 추세 신호 계산 (LLM 불필요·순수 함수)
#
# 이 파일의 역할:
#   - 정형 사실 데이터(chunking.load_rows 와 같은 행들)를 (문항, 응답라벨)별
#     '연도 시계열'로 묶고, 최신 전년대비 변화(YoY, %p)로 추세 신호를 매긴다.
#       🟢 up   = 뚜렷한 상승(+threshold %p 이상)
#       🟡 flat = 보합(|변화| < threshold)
#       🔴 down = 뚜렷한 하락(-threshold %p 이하)
#   - 색은 '추세 방향'만 뜻한다(좋음/나쁨 같은 가치판단 아님 — "추측은 데이터가 아니다").
#   - % 단위 + 2개년 이상일 때만 신호를 매긴다(그 외는 추세선만).
#
#   원칙: 데이터에 실제 있는 값·출처만 쓴다. 추정/보간 없음. 값이 비거나
#         숫자가 아니면 그 (라벨,연도) 점은 빼고, 점이 2개 미만이면 신호 없음.
#
# 검증: uv run python -m pytest tests/test_signals.py -q   (LLM 불필요·결정적)
# -----------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_THRESHOLD_PP = 3.0   # 이 %p 이상 변하면 상승/하락, 그 미만은 보합

# 신호 → 화면 표시(이모지/한글). app 과 테스트가 함께 쓴다.
SIGNAL_EMOJI = {"up": "🟢", "flat": "🟡", "down": "🔴"}
SIGNAL_TEXT = {"up": "상승", "flat": "보합", "down": "하락"}


@dataclass
class Point:
    year: int
    value: float


@dataclass
class Series:
    """ 한 (문항, 응답라벨)의 연도 시계열. """
    label: str                 # 응답 라벨(예: '인지', '관심 있음')
    unit: str                  # 단위('%' 일 때만 신호)
    points: list[Point]        # 연도 오름차순
    source: str = ""           # 최신 연도 출처(인용용)
    page: str = ""

    @property
    def latest(self) -> Point:
        return self.points[-1]

    @property
    def prev(self) -> Point | None:
        return self.points[-2] if len(self.points) >= 2 else None

    @property
    def delta(self) -> float | None:
        """ 최신-직전(=가장 최근 두 시점) 변화(%p). 점이 1개면 None. """
        if self.prev is None:
            return None
        return round(self.latest.value - self.prev.value, 1)

    def signal(self, threshold_pp: float = DEFAULT_THRESHOLD_PP) -> str | None:
        """ 'up'/'flat'/'down' 또는 None(신호 없음: 비% 단위·점 1개). """
        if self.unit != "%" or self.delta is None:
            return None
        if self.delta >= threshold_pp:
            return "up"
        if self.delta <= -threshold_pp:
            return "down"
        return "flat"


@dataclass
class Indicator:
    """ 한 문항(std_id)과 그 응답 라벨 시계열들. """
    std_id: str
    label: str                 # std_label(문항 제목)
    category: str
    summary: str
    series: list[Series]

    @property
    def max_abs_delta(self) -> float:
        deltas = [abs(s.delta) for s in self.series if s.delta is not None]
        return max(deltas) if deltas else 0.0


def _parse_year(y) -> int | None:
    try:
        return int(str(y).strip())
    except (TypeError, ValueError):
        return None


def _parse_value(v) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _page_str(row: dict) -> str:
    def as_int(key):
        s = (row.get(key) or "").strip()
        return int(s) if s.isdigit() else None
    ps = [x for x in (as_int("page_start"), as_int("page_end")) if x is not None]
    if not ps:
        return ""
    lo, hi = min(ps), max(ps)
    return str(lo) if lo == hi else f"{lo}-{hi}"


def compute_signals(rows: list[dict],
                    threshold_pp: float = DEFAULT_THRESHOLD_PP) -> list[Indicator]:
    """ 사실 행들 → 문항별 추세 지표 목록. 변화 큰(|Δ|) 순으로 정렬해 돌려준다. """
    # std_id → {메타, labels: {label: {year: (value, unit, source, page)}}}
    by_std: dict[str, dict] = {}
    for r in rows:
        sid = (r.get("std_id") or "").strip()
        year = _parse_year(r.get("year"))
        value = _parse_value(r.get("value"))
        if not sid or year is None or value is None:
            continue
        label = (r.get("std_response_label") or r.get("response_label") or "").strip() or "(전체)"
        unit = (r.get("unit") or "").strip()
        d = by_std.setdefault(sid, {
            "std_label": (r.get("std_label") or sid).strip(),
            "category": (r.get("category") or "").strip() or "(미분류)",
            "summary": (r.get("question_summary") or "").strip(),
            "labels": {},
        })
        # 같은 (label, year) 가 중복되면 마지막 값이 이긴다(데이터상 거의 없음).
        d["labels"].setdefault(label, {})[year] = (value, unit, _page_str(r),
                                                   (r.get("source") or "").strip())

    indicators: list[Indicator] = []
    for sid, d in by_std.items():
        series_list: list[Series] = []
        for label, yvals in d["labels"].items():
            if len(yvals) < 2:               # 추세를 보려면 2개년 이상
                continue
            years = sorted(yvals)
            points = [Point(y, yvals[y][0]) for y in years]
            _, unit, page, src = yvals[years[-1]]   # 최신 연도의 단위·출처
            series_list.append(Series(label, unit, points, src, page))
        if not series_list:
            continue
        # 변화 큰 라벨이 위로
        series_list.sort(key=lambda s: abs(s.delta) if s.delta is not None else 0.0,
                         reverse=True)
        indicators.append(Indicator(sid, d["std_label"], d["category"],
                                    d["summary"], series_list))

    indicators.sort(key=lambda ind: ind.max_abs_delta, reverse=True)
    return indicators


def summarize(indicators: list[Indicator],
              threshold_pp: float = DEFAULT_THRESHOLD_PP) -> dict[str, int]:
    """ 전체 시계열의 신호 개수 집계: {'up':n,'flat':n,'down':n}. """
    counts = {"up": 0, "flat": 0, "down": 0}
    for ind in indicators:
        for s in ind.series:
            sig = s.signal(threshold_pp)
            if sig:
                counts[sig] += 1
    return counts


def categories(indicators: list[Indicator]) -> list[str]:
    """ 등장 카테고리(지표 많은 순). """
    counts: dict[str, int] = {}
    for ind in indicators:
        counts[ind.category] = counts.get(ind.category, 0) + 1
    return sorted(counts, key=lambda c: counts[c], reverse=True)
