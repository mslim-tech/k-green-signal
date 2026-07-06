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

# 단년 변동이 이 %p 를 넘으면 '이례적으로 큼' — 설문 변경(보기 개편 등) 가능성이 있어
# 헤드라인('신뢰 높음')에서 빼고 '검증 필요'로 분리한다. 근거: 설계 동일 구간(2024→2025)
# 신호의 |Δ| 중앙값 6.6%p·평균 7.3%p, |Δ|>15%p 는 상위 10%(실측). 실제 변화가 아니라고
# 단정하지 않되, 원문 확인 전에는 의사결정 헤드라인으로 올리지 않는다.
LARGE_YOY_PP = 15.0

# 신호 → 화면 표시(이모지/한글). app 과 테스트가 함께 쓴다.
SIGNAL_EMOJI = {"up": "🟢", "flat": "🟡", "down": "🔴"}
SIGNAL_TEXT = {"up": "상승", "flat": "보합", "down": "하락"}

# 방법론 노트가 다루는 척도 전환 경계(2023 4점척도/2분할 → 2024 2점척도). 이 구간을
# 건너뛰는 최신 전이는 '실제 변화'가 아닐 수 있어 신호를 보류한다(RAG 방법론 주석과 일관).
SCALE_BREAK_FROM, SCALE_BREAK_TO = 2023, 2024

# 큐레이션된 척도변경 지표(std_id) 로더 — 없으면 빈 dict(순수 계산은 계속 동작).
try:
    from rag.curate.methodology import caveats_by_std_id as _caveats_by_std_id
except Exception:   # pragma: no cover
    def _caveats_by_std_id() -> dict:
        return {}

# 집계·비응답 보기 — 순위·추세 신호에서 제외한다(RAG 답변의 순위 제외 기준과 동일).
# 보수적으로 매칭: 명백한 집계/비응답만. '관심 없음' 같은 실제 응답은 건드리지 않는다.
_AGG_EXACT = {"없음", "모름", "무응답", "없음/모름/무응답", "모름/무응답", "무응답/모름"}
_AGG_CONTAINS = ("기타", "소계", "합계", "총계", "무응답")


def is_aggregation_label(label: str) -> bool:
    """ '기타·소계·합계·무응답·(단독)없음/모름' 등 집계·비응답 보기인가. """
    lab = (label or "").strip()
    if lab in _AGG_EXACT:
        return True
    return any(tok in lab for tok in _AGG_CONTAINS)


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
    caveat_break: bool = False  # 최신 전이가 척도변경 경계를 건너뛰나(신호 보류)

    @property
    def is_aggregate(self) -> bool:
        """ 집계·비응답 보기(기타·소계·무응답 등) — 신호를 매기지 않는다. """
        return is_aggregation_label(self.label)

    @property
    def spans_scale_break(self) -> bool:
        """ 최신 전이가 2023→2024(2024 조사 개편 경계)를 가로지르나. 이 구간의 변화는
            설문 개편 영향일 수 있어 '실제 변화' 헤드라인에서 빼고 '해석 유의'로 분리한다. """
        return (self.prev is not None
                and self.prev.year <= SCALE_BREAK_FROM <= self.latest.year
                and self.latest.year >= SCALE_BREAK_TO)

    @property
    def block_reason(self) -> str:
        """ 신호를 보류한 이유(있으면). 카드가 🔴/🟢 대신 이 사유를 보여준다. """
        if self.is_aggregate:
            return "집계·비응답 항목"
        if self.caveat_break:
            return "척도 변경 구간"
        return ""

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

    @property
    def is_yoy(self) -> bool:
        """ 최신 두 점이 '인접 연도'(간격 1)인가. 그래야 변화가 진짜 전년대비(YoY)다.
            간격이 벌어지면(예: 2017→2023) 6년치를 한 스텝처럼 보여 '가짜 점프'가 된다. """
        return self.prev is not None and (self.latest.year - self.prev.year) == 1

    def signal(self, threshold_pp: float = DEFAULT_THRESHOLD_PP) -> str | None:
        """ 'up'/'flat'/'down' 또는 None(신호 없음: 비% 단위·점 1개·최신 두 점이
            인접 연도 아님·집계항목·척도변경 구간). 인접 연도일 때만 진짜 YoY 로 신호를 매긴다. """
        if self.is_aggregate or self.caveat_break:
            return None
        if self.unit != "%" or self.delta is None or not self.is_yoy:
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
        # 인접 연도(진짜 YoY)이면서 신호 대상인 시계열만으로 정렬한다(집계·척도변경·가짜 점프 제외).
        deltas = [abs(s.delta) for s in self.series
                  if s.delta is not None and s.is_yoy and not s.is_aggregate and not s.caveat_break]
        return max(deltas) if deltas else 0.0

    @property
    def non_aggregate_series(self) -> list["Series"]:
        return [s for s in self.series if not s.is_aggregate]

    @property
    def is_binary_mirror(self) -> bool:
        """ 이진 상보 문항인가(예: 인지/비인지, 알고있음/모르고있음). 공유 연도마다
            두 라벨 값 합이 ~100 이면 서로 거울상 — 한 쪽만 세어 중복 계수를 막는다. """
        ns = self.non_aggregate_series
        if len(ns) != 2:
            return False
        av = {p.year: p.value for p in ns[0].points}
        bv = {p.year: p.value for p in ns[1].points}
        shared = set(av) & set(bv)
        if not shared:
            return False
        return all(abs(av[y] + bv[y] - 100) <= 1.5 for y in shared)


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


def dataset_years(rows: list[dict]) -> list[int]:
    """ 데이터에 값이 있는 연도들(오름차순). '연속 추적' 기준(모든 연도 커버) 계산용. """
    ys = set()
    for r in rows:
        y = _parse_year(r.get("year"))
        if y is not None and _parse_value(r.get("value")) is not None:
            ys.add(y)
    return sorted(ys)


def compute_signals(rows: list[dict],
                    threshold_pp: float = DEFAULT_THRESHOLD_PP,
                    min_coverage: int = 2,
                    caveated_ids: set[str] | None = None) -> list[Indicator]:
    """ 사실 행들 → 문항별 추세 지표 목록. 변화 큰(|Δ|) 순으로 정렬해 돌려준다.
        min_coverage: 한 (문항,라벨) 시계열이 이 개수 이상의 연도 값을 가져야 포함한다.
            2(기본)=추세 가능한 모두. len(dataset_years)=모든 연도에 값 있는 '연속 추적' 항목만.
        caveated_ids: 척도변경 문항(std_id) 집합. None 이면 방법론 노트에서 로드. 이 문항의
            척도경계('23→'24)를 건너뛰는 최신 전이는 신호를 보류한다(RAG 방법론 주석과 일관). """
    caveated = set(caveated_ids) if caveated_ids is not None else set(_caveats_by_std_id())
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
            if len(yvals) < max(2, min_coverage):   # 최소 커버리지(연속 추적 기준)
                continue
            years = sorted(yvals)
            points = [Point(y, yvals[y][0]) for y in years]
            _, unit, page, src = yvals[years[-1]]   # 최신 연도의 단위·출처
            s = Series(label, unit, points, src, page)
            # 척도변경 문항: 최신 전이가 척도경계('23→'24)를 건너뛰면 신호 보류(⚠️).
            if (sid in caveated and len(points) >= 2
                    and points[-2].year == SCALE_BREAK_FROM
                    and points[-1].year == SCALE_BREAK_TO):
                s.caveat_break = True
            series_list.append(s)
        if not series_list:
            continue
        # 변화 큰 라벨이 위로
        series_list.sort(key=lambda s: abs(s.delta) if s.delta is not None else 0.0,
                         reverse=True)
        indicators.append(Indicator(sid, d["std_label"], d["category"],
                                    d["summary"], series_list))

    indicators.sort(key=lambda ind: ind.max_abs_delta, reverse=True)
    return indicators


def signaled_movers(indicators: list[Indicator],
                    threshold_pp: float = DEFAULT_THRESHOLD_PP) -> list[tuple]:
    """ 신호가 매겨진 (지표, 시계열) 목록 — |Δ| 큰 순. 의사결정용 '실제 변화'만 남긴다:
        집계·비응답·척도변경 구간은 signal()==None 이라 이미 빠지고, 이진 상보 문항은
        대표 1개(최신값 큰 쪽)만 세어 거울상 중복을 막는다. """
    out: list[tuple] = []
    for ind in indicators:
        sigs = [s for s in ind.series if s.signal(threshold_pp)]
        if not sigs:
            continue
        if ind.is_binary_mirror:
            sigs = sorted(sigs, key=lambda s: s.latest.value, reverse=True)[:1]
        out.extend((ind, s) for s in sigs)
    out.sort(key=lambda t: abs(t[1].delta) if t[1].delta is not None else 0.0, reverse=True)
    return out


@dataclass
class YearChange:
    """ '전년대비 변화 폭이 큰 해' 한 건 — 외부 맥락 검색의 대상 연도 후보. """
    year: int                  # 변화가 도착한 해(= prev_year + 1)
    prev_year: int
    delta: float               # value[year] - value[prev_year] (%p)
    indicator_label: str       # 어느 문항에서
    series_label: str          # 어느 응답 라벨에서

    @property
    def abs_delta(self) -> float:
        return abs(self.delta)


def big_change_years(indicators: list[Indicator],
                     threshold_pp: float = 5.0,
                     caveated_ids: set[str] | None = None) -> list[YearChange]:
    """ 선택 지표들에서 '전년대비 변화 폭이 큰 해'를 자동 탐지한다(외부 맥락 검색 대상).
        - 인접 연도(간격 1)·% 단위·비집계 시계열만 본다('가짜 점프'·집계 제외).
        - 척도변경 문항의 '23→'24 전이는 측정방식 변화일 수 있어 제외(신호 규칙과 일관).
        - 같은 해가 여러 지표에서 크면 |Δ| 가장 큰 것을 대표로 남긴다(연도 1건).
        - |Δ| 큰 순으로 정렬해 돌려준다. 추정/보간 없이 실제 값 차이만 쓴다. """
    caveated = set(caveated_ids) if caveated_ids is not None else set(_caveats_by_std_id())
    best: dict[int, YearChange] = {}
    for ind in indicators:
        for s in ind.series:
            if s.is_aggregate or s.unit != "%":
                continue
            for a, b in zip(s.points, s.points[1:]):
                if b.year - a.year != 1:              # 인접 연도만(진짜 전년대비)
                    continue
                if (ind.std_id in caveated
                        and a.year == SCALE_BREAK_FROM and b.year == SCALE_BREAK_TO):
                    continue                          # 척도 변경 구간 — 실제 변화 아닐 수 있음
                d = round(b.value - a.value, 1)
                if abs(d) < threshold_pp:
                    continue
                cur = best.get(b.year)
                if cur is None or abs(d) > cur.abs_delta:
                    best[b.year] = YearChange(b.year, a.year, d, ind.label, s.label)
    return sorted(best.values(), key=lambda c: c.abs_delta, reverse=True)


def caveat_breaks(indicators: list[Indicator]) -> list[tuple]:
    """ 척도변경 구간이라 신호를 보류한 (지표, 시계열) — '⚠️ 해석 유의' 섹션용.
        원값 변화(Δ)는 보여주되 '실제 변화 아님(척도 변경)'으로 분리해 헤드라인에서 뺀다. """
    out = [(ind, s) for ind in indicators for s in ind.series
           if s.caveat_break and not s.is_aggregate and s.unit == "%" and s.delta is not None]
    out.sort(key=lambda t: abs(t[1].delta), reverse=True)
    return out


def summarize(indicators: list[Indicator],
              threshold_pp: float = DEFAULT_THRESHOLD_PP) -> dict[str, int]:
    """ 신호 개수 집계: {'up':n,'flat':n,'down':n}. 집계·척도변경·거울상 중복은 제외
        (signaled_movers 기준) — 화면 카운트가 의사결정에 유의미하도록. """
    counts = {"up": 0, "flat": 0, "down": 0}
    for _ind, s in signaled_movers(indicators, threshold_pp):
        counts[s.signal(threshold_pp)] += 1
    return counts


def categories(indicators: list[Indicator]) -> list[str]:
    """ 등장 카테고리(지표 많은 순). """
    counts: dict[str, int] = {}
    for ind in indicators:
        counts[ind.category] = counts.get(ind.category, 0) + 1
    return sorted(counts, key=lambda c: counts[c], reverse=True)
