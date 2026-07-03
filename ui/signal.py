# ui/signal.py
# -----------------------------------------------------------------------------
# 🚦 실시간 신호등 대시보드 — 앱의 랜딩 화면 (app.py 에서 분리).
#   - 정형 사실 데이터를 연도별 추세 신호(🟢🟡🔴)로 시각화하고,
#     '비교 가능한 실제 변화'는 헤드라인, 2023→'24 개편구간/척도변경은 '해석 유의'로 분리한다.
#   - 순수 계산은 rag/signals.py, 화면/차트만 여기.
# -----------------------------------------------------------------------------
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from rag.retrieval import chunking
from rag import signals
from rag.curate.methodology import caveats_by_std_id as _caveats_by_std_id


# 핵심 정책 지표 탭: 사용자가 추적하고 싶어 한 지표를 std_id 로 묶는다.
# 데이터에 실제 있는 std_id 만 그린다(없으면 '데이터 없음'으로 정직하게 표시 — 추측 금지).
PRIORITY_GROUPS = [
    ("주요 인증제도 인지도 추이", ["녹색제품_인지도", "환경표지_인지도",
                                    "환경성적표지_인지도", "저탄소제품_인지도",
                                    "녹색매장_인지도", "그린카드_인지도"]),
    ("친환경 제품 구매·관심도", ["친환경제품_구매경험", "친환경제품_관심도", "환경문제_관심도"]),
    ("그린카드 성과 지표", ["그린카드_발급사용의향", "그린카드_사용여부",
                            "그린카드_전반만족도", "그린카드_포인트기부의향"]),
    ("경제적 가치(추가 지불의사)", ["친환경제품_추가지불의향"]),
]


def _trend_altair(series_list):
    """ signals.Series 목록 → 연도별 추세 멀티라인 Altair 차트(없으면 None).
        연도축을 '순서형(범주)'으로 둬 등장 연도를 한 번씩 균등 배치한다(예전 정량축은
        2024·2025 사이에 눈금이 중복 표시되던 버그). 값이 없는 연도는 null 로 채워
        선을 '끊어' 없는 구간을 직선으로 잇지 않는다(보간/추측 없음). 점은 값 있는 해에만. """
    import altair as alt
    import pandas as pd

    years = sorted({p.year for s in series_list for p in s.points})
    if not years:
        return None
    # 각 시계열에 대해 전체 연도축을 채운다(없는 해는 값 None → 선 끊김).
    recs = []
    for s in series_list:
        present = {p.year: p.value for p in s.points}
        for y in years:
            recs.append({"연도": str(y), "값": present.get(y),
                         "응답": s.label, "단위": s.unit or ""})
    df = pd.DataFrame(recs)
    order = [str(y) for y in years]
    # 범례는 최신값 큰 순으로(위 라인이 먼저), 라벨은 잘리지 않게(labelLimit=0).
    latest = {s.label: s.points[-1].value for s in series_list if s.points}
    legend_order = sorted(latest, key=lambda k: latest[k], reverse=True)
    chart = (
        alt.Chart(df)
        .mark_line(point=alt.OverlayMarkDef(size=45), strokeWidth=2.5)
        .encode(
            x=alt.X("연도:O", sort=order, title="연도",
                    axis=alt.Axis(labelAngle=0)),
            y=alt.Y("값:Q", title="%"),
            color=alt.Color("응답:N", title="응답 항목", sort=legend_order,
                            legend=alt.Legend(orient="bottom", columns=1, labelLimit=0,
                                              symbolType="stroke")),
            tooltip=[alt.Tooltip("응답:N", title="응답"),
                     alt.Tooltip("연도:O", title="연도"),
                     alt.Tooltip("값:Q", title="값"),
                     alt.Tooltip("단위:N", title="단위")],
        )
        .properties(height=320)
    )
    return chart


# 지표별 비교 유의 경고(std_id → 캡션): 연도 간 척도·정의가 달라 시계열을 곧이곧대로
# 비교하면 안 되는 지표에 표시한다. 데이터는 그대로 두고 '해석 유의'만 알린다.
# 단일 소스는 curation/methodology_notes.json(청킹도 같은 파일을 지식청크로 인덱싱) —
# 여기서 하드코딩하지 않고 로드해 드리프트를 없앤다(로더는 파일 상단에서 import).
INDICATOR_CAVEATS: dict[str, str] = _caveats_by_std_id()


def _md_escape(text: str) -> str:
    """ 방법론 노트 같은 '평문' 캡션이 마크다운 서식으로 오해되지 않게 특수문자를 이스케이프한다.
        특히 물결표: 노트의 "'24~는 … (=82.2%~)" 처럼 ~ 가 쌍이 되면 Streamlit(remark-gfm)이
        취소선(~…~)으로 먹어 '2024 2점척도=82.2%' 같은 핵심 설명이 지워진 듯 렌더된다.
        백슬래시를 먼저 이스케이프한 뒤 나머지를 처리한다. """
    for ch in "\\`*_~[]$":
        text = text.replace(ch, "\\" + ch)
    return text


def _render_indicator_card(ind, threshold, max_series: int = 5):
    """ 한 지표(Indicator)를 카드로: 멀티라인 차트 + 응답별 최신값·신호 + 출처.
        라벨이 많으면 변화 큰 max_series 개만 차트에 그린다(compute_signals 가 정렬해 둠 —
        과밀·범례 잘림을 줄여 가독성을 높인다). """
    top_series = ind.series[:max_series]
    with st.container(border=True):
        st.markdown(f"**{ind.label}**")
        if ind.summary:
            st.caption(ind.summary)
        caveat = INDICATOR_CAVEATS.get(ind.std_id)
        if caveat:
            st.caption(_md_escape(caveat))
        chart = _trend_altair(top_series)
        if chart is not None:
            st.altair_chart(chart, width="stretch")
        for s in top_series:
            sig = s.signal(threshold)
            em = signals.SIGNAL_EMOJI.get(sig, "⚠️" if s.block_reason else "·")
            if sig:
                tail = f" ({s.delta:+}%p {signals.SIGNAL_TEXT[sig]})"
            elif s.block_reason:
                # 집계·비응답 또는 척도변경 구간 → 인접 연도라도 신호를 매기지 않는다.
                extra = f"Δ{s.delta:+}%p, " if (s.delta is not None and s.unit == "%") else ""
                tail = f" ({extra}{s.block_reason} — 신호 보류)"
            elif s.delta is not None and s.unit != "%":
                tail = f" (Δ{s.delta:+}, 비% 단위)"
            elif s.delta is not None:
                tail = f" (Δ{s.delta:+}%p, 비인접년 — 신호 없음)"
            else:
                tail = ""
            st.write(f"{em} {s.label}: {s.latest.value}{s.unit}{tail}")
        head = top_series[0]
        st.caption(f"[출처: {head.source} p.{head.page}]")
        if len(ind.series) > max_series:
            st.caption(f"… 외 {len(ind.series) - max_series}개 응답 항목은 변화가 작아 생략했습니다.")


def _summary_headline_series(ind):
    """ 요약(현재 성적표)에 쓸 대표 시계열: % 단위 중 커버리지 최장(동률이면 최신값 큰) 것.
        %가 없으면 그냥 첫 시계열. compute_signals 정렬과 무관하게 '대표 라인'을 고른다. """
    cands = [s for s in ind.series if s.unit == "%"] or ind.series
    return max(cands, key=lambda s: (len(s.points), s.latest.value))


def _mover_line(ind, s, threshold):
    """ 요약용 한 줄: 지표·응답 · 현재값(#1) · 전년대비(#2) · 12개년 평균 대비(#3) · 출처.
        데이터에 실제 있는 값만 쓴다(추측 없음). 척도 변경 지표는 ⚠️로 해석 유의를 알린다. """
    sig = s.signal(threshold)
    em = signals.SIGNAL_EMOJI.get(sig, "·")
    vals = [p.value for p in s.points]
    mean = round(sum(vals) / len(vals), 1)
    pos = "평균 상회" if s.latest.value >= mean else "평균 하회"
    cav = " · ⚠️척도변경 주의" if ind.std_id in INDICATOR_CAVEATS else ""
    delta_txt = f"전년대비 **{s.delta:+}%p**" if (s.delta is not None and s.is_yoy) else "전년대비 —(비인접)"
    st.markdown(
        f"{em} **{ind.label}** · {s.label}  \n"
        f"현재 **{s.latest.value}{s.unit}**({s.latest.year}) · {delta_txt} · "
        f"{s.points[0].year}~평균 {mean}{s.unit}({pos}){cav}")
    st.caption(f"[출처: {s.source} p.{s.page}]")


def _render_status_scorecards(inds, max_cards: int = 6):
    """ 지표별 '현재 성적표': 검색어 관련 지표 각각을 카드로 —
        #1 최신값(현재 성적표) · #2 전년대비(↑↓, st.metric 이 화살표/색 자동) ·
        #3 보유 연도 평균 대비(벤치마크). 데이터에 실제 있는 값만 쓴다(추측 없음). """
    # 대표 라인(커버리지 최장) 기준으로 정렬 — 주요 헤드라인 지표가 앞에 오게.
    picks = sorted(inds, key=lambda i: len(_summary_headline_series(i).points),
                   reverse=True)[:max_cards]
    st.markdown("**📋 지표별 현재 성적표**")
    st.caption("각 지표의 최신값 · 전년대비(↑↓) · 보유 연도 평균 대비")
    cols = st.columns(3)
    for i, ind in enumerate(picks):
        s = _summary_headline_series(ind)
        vals = [p.value for p in s.points]
        mean = round(sum(vals) / len(vals), 1)
        pos = "평균 상회" if s.latest.value >= mean else "평균 하회"
        yoy = f"{s.delta:+}%p" if (s.delta is not None and s.is_yoy) else None
        with cols[i % 3]:
            st.metric(
                label=f"{ind.label} · {s.label}",
                value=f"{s.latest.value}{s.unit} ({s.latest.year})",
                delta=yoy,                     # ↑/↓ 화살표·색은 metric 이 자동
                delta_color="normal" if yoy else "off",
            )
            span = f"{s.points[0].year}~{s.points[-1].year}"
            note = "" if s.is_yoy or s.delta is None else " · 전년대비 —(비인접)"
            cav = " · ⚠️척도변경" if ind.std_id in INDICATOR_CAVEATS else ""
            st.caption(f"{span} 평균 {mean}{s.unit} → 현재 {s.latest.value}{s.unit} **{pos}**"
                       f"{note}{cav}  \n[출처: {s.source} p.{s.page}]")
    if len(inds) > max_cards:
        st.caption(f"… 외 {len(inds) - max_cards}개 지표는 아래 탭에서 확인하세요.")


def _top1_latest(ind):
    """ 이 지표의 '최신 연도 1순위'(=최신 연도에서 값이 가장 큰 응답). (연도, 라벨, 값, 출처, page).
        집계·비응답 라벨('기타'·'없음/모름' 등)은 순위에서 제외 — _raw_top1·RAG 프롬프트와 같은 규칙. """
    ly = max(p.year for s in ind.series for p in s.points)
    best = None
    for s in ind.series:
        if signals.is_aggregation_label(s.label):
            continue
        for p in s.points:
            if p.year == ly and (best is None or p.value > best[2]):
                best = (ly, s.label, p.value, s.source, s.page)
    return best


def _pick_context(inds, key_sub, terms):
    """ 판단기준/비구매이유 같은 '맥락' 지표를 고른다: 1순위(복수응답 제외) 중 →
        쿼리 매칭 우선 → 친환경제품_ 플래그십 우선 → 커버리지 최장. 없으면 None. """
    cands = [i for i in inds if key_sub in i.std_id and "복수응답" not in i.std_id]
    if not cands:
        return None
    if terms:
        matched = [i for i in cands if _match_terms(_ind_haystack(i), terms)]
        if matched:
            cands = matched
    flag = [i for i in cands if i.std_id.startswith("친환경제품_")]
    pool = flag or cands
    return max(pool, key=lambda i: len(i.series[0].points))


def _raw_top1(rows, needles, terms):
    """ needles 든 문항(terms 매칭 우선)의 '최신 연도 1순위'(집계·비응답 제외)를 raw 행에서 뽑는다.
        판단기준·비구매이유가 단일 연도만 조사돼 compute_signals(2년+)에 안 잡힐 때의 폴백.
        → (문항명, 연도, 응답라벨, 값, 출처, page) 또는 None. 실제 있는 값만(추측 없음). """
    cands = _raw_indicators_by_label(rows, needles)   # [(sid, label, years)]
    if terms:
        matched = [c for c in cands if _match_terms(c[1].lower(), terms)]
        if matched:
            cands = matched
    if not cands:
        return None
    sid, lab, yrs = max(cands, key=lambda c: len(c[2]))
    if not yrs:
        return None
    year = yrs[-1]
    best = None   # (값, 응답라벨, 출처, page)
    for r in rows:
        if (r.get("std_id") or "").strip() != sid or (r.get("year") or "").strip() != year:
            continue
        rl = (r.get("std_response_label") or r.get("response_label") or "").strip()
        if not rl or signals.is_aggregation_label(rl):
            continue
        try:
            v = float((r.get("value") or "").strip())
        except ValueError:
            continue
        if best is None or v > best[0]:
            ps, pe = (r.get("page_start") or "").strip(), (r.get("page_end") or "").strip()
            page = f"{ps}-{pe}" if pe and pe != ps else ps
            best = (v, rl, (r.get("source") or "").strip(), page)
    return (lab, year, best[1], best[0], best[2], best[3]) if best else None


def _render_drivers_barriers(full_inds, terms, rows):
    """ 행동 동기·장애(#5): 구매 판단 기준 1순위 + 비구매 이유 1순위. 실제 지표만(추측 없음).
        다년 지표가 있으면 그걸, 없으면(그 문항이 단일 연도만 조사됨) 그 해 raw 값으로 1순위를 뽑는다. """
    crit, barr = _pick_context(full_inds, "판단기준", terms), _pick_context(full_inds, "비구매이유", terms)
    crit_lab, crit_t = (crit.label, _top1_latest(crit)) if crit else (None, None)
    barr_lab, barr_t = (barr.label, _top1_latest(barr)) if barr else (None, None)
    if crit_t is None and (rt := _raw_top1(rows, JUDGMENT_NEEDLES, terms)):
        crit_lab, crit_t = rt[0], rt[1:]     # (연도, 라벨, 값, 출처, page)
    if barr_t is None and (rt := _raw_top1(rows, BARRIER_NEEDLES, terms)):
        barr_lab, barr_t = rt[0], rt[1:]
    if not crit_t and not barr_t:
        return
    st.markdown("**🧭 행동 동기 · 장애 요인**")
    st.caption("구매를 이끈 판단 기준 1순위 · 구매를 막은 비구매 이유 1순위")
    cc, cb = st.columns(2)
    with cc:
        if crit_t:
            st.markdown(f"🎯 **구매 판단 1순위** ({crit_t[0]})  \n{crit_t[1]} · **{crit_t[2]}%**")
            st.caption(f"{crit_lab} · [출처: {crit_t[3]} p.{crit_t[4]}]")
        else:
            st.caption("판단 기준 데이터 없음")
    with cb:
        if barr_t:
            st.markdown(f"🚧 **비구매 이유 1순위** ({barr_t[0]})  \n{barr_t[1]} · **{barr_t[2]}%**")
            st.caption(f"{barr_lab} · [출처: {barr_t[3]} p.{barr_t[4]}]")
        else:
            st.caption("비구매 이유 데이터 없음")


# 프로젝트 루트 = ui/ 의 상위. (이 파일이 app.py 에서 ui/ 로 분리되며 부모가 한 단계 깊어졌다.)
_EXTERNAL_CONTEXT_PATH = Path(__file__).resolve().parent.parent / "curation" / "external_context.json"


@st.cache_data
def _load_external_context():
    """ 큐레이션된 외부 맥락(뉴스) 항목. 파일 없거나 깨지면 빈 목록(기능만 비활성). """
    try:
        return json.loads(_EXTERNAL_CONTEXT_PATH.read_text(encoding="utf-8")).get("entries", [])
    except Exception:
        return []


_INFLECTION_PP = 5.0   # 인접 연도 |전년대비|가 이 %p 이상이면 '변곡점'으로 표시(⭐)


def _render_inflection_context(inds, query):
    """ 변곡점 × 외부 맥락(#6 해석): 검색어에 관련된 그해 외부 이슈마다, 대표 지표가 그해
        어떻게 움직였는지(전년대비 %p)를 나란히 보여 '이슈 ↔ 데이터 변화'를 대조한다.
        인접 연도 |Δ|≥임계면 ⭐변곡점 표시. ⚠️ 척도 변경 구간('22~'24 caveat 지표)의 변화는
        측정 방식 변화일 수 있어 변곡점으로 보지 않는다. 상관/맥락이며 인과 단정 아님. """
    entries = _load_external_context()
    if not entries:
        return
    # 대표 지표 = 커버리지(연도 수) 최장. 그 대표 시계열 값을 연도별로.
    ind = max(inds, key=lambda i: len(_summary_headline_series(i).points))
    s = _summary_headline_series(ind)
    byyear = {p.year: p.value for p in s.points}
    # 이벤트 매칭은 검색어+관련 지표 전체로(넓게), 데이터 변화는 대표 지표로(구체).
    hay = (query + " " + " ".join(f"{i.label} {i.std_id}" for i in inds)).lower()
    evs = sorted([e for e in entries if any(kw.lower() in hay for kw in e.get("match", []))],
                 key=lambda e: e.get("year", 0))
    if not evs:
        return
    caveated = ind.std_id in INDICATOR_CAVEATS
    st.divider()
    st.markdown("**📈 변곡점 × 외부 맥락**")
    st.caption(f"검색어 관련 그해 환경 이슈와, 대표 지표 '{ind.label}·{s.label}'의 그해 변화를 "
               "대조합니다. ⭐는 그해가 데이터 변곡점(전년대비 큰 변화)임을 뜻합니다. "
               "상관·맥락이며 인과를 단정하지 않습니다. "
               "💡 데이터×사건을 엮은 **상세 해석은 '데이터 기반 제언' 모드**에서 받을 수 있습니다.")
    # 설문 데이터가 있는 연도(대조 가능)와 그 이전(배경 맥락)을 분리해 '데이터 없음' 노이즈를 없앤다.
    min_dy = min(byyear) if byyear else None
    overlap = [e for e in evs if min_dy is not None and e.get("year", 0) >= min_dy]
    background = [e for e in evs if min_dy is None or e.get("year", 0) < min_dy]
    for e in overlap:
        y = e.get("year")
        head = f"· **{y}** {e.get('title','')} — [출처: {e.get('source','')}]({e.get('url','')})"
        prev = [yr for yr in byyear if yr < y]
        if y in byyear and prev:
            py = max(prev)
            d = round(byyear[y] - byyear[py], 1)
            gap = y - py
            if caveated and y in (2023, 2024):
                tail = f"　↔ 데이터 {py}→{y}: {byyear[py]}→{byyear[y]} (⚠️ 척도 변경 구간 — 해석 유의)"
            else:
                star = " ⭐변곡점" if (gap == 1 and abs(d) >= _INFLECTION_PP) else ""
                near = "" if gap == 1 else f"({py}→{y}, {gap}년 간격)"
                tail = f"　↔ 데이터 전년대비 **{d:+}%p**{near}{star}"
        else:
            tail = f"　↔ 데이터 {y}년 {byyear[y]}{s.unit or ''} (직전 연도 없음)" if y in byyear \
                else "　↔ 이 지표엔 그해 데이터 없음"
        st.markdown(head + "  \n" + tail)
    if background:
        st.caption("🕰 배경 (설문 데이터 이전 — 대조할 값은 없지만 '어떻게 여기까지 왔는가' 맥락):")
        for e in background:
            st.markdown(f"· {e.get('year')} {e.get('title','')} — "
                        f"[출처: {e.get('source','')}]({e.get('url','')})")


def _render_query_summary(inds, threshold, query, ds_years, full_inds, rows):
    """ 검색어 요약(결론 먼저): 근거 있는 signals 만으로 ①키워드 요약 ②상승/보합/하락 수
        ③최대 상승 Top3 ④최대 하락 Top3. '추측은 데이터가 아니다' — 하위집단/외부뉴스는 안 만든다. """
    with st.container(border=True):
        st.markdown(f"### 🎯 '{query}' 핵심 요약 (결론)")
        st.caption(f"관련 지표 {len(inds)}개 · 데이터 연도 {ds_years[0]}~{ds_years[-1]} · "
                   "신호는 인접 연도(전년대비) 기준")

        counts = signals.summarize(inds, threshold)
        c1, c2, c3 = st.columns(3)
        c1.metric("🟢 상승", counts["up"])
        c2.metric("🟡 보합", counts["flat"])
        c3.metric("🔴 하락", counts["down"])

        st.divider()
        _render_status_scorecards(inds)   # 지표별 현재 성적표(#1·#2·#3)
        st.divider()

        # 설계 동일 구간(2023→2024 개편 제외)만 헤드라인 — 집계·거울상·척도변경은 이미 제외됨.
        movers = [(i, s) for i, s in signals.signaled_movers(inds, threshold)
                  if not s.spans_scale_break]
        ups = sorted([m for m in movers if m[1].delta > 0],
                     key=lambda m: m[1].delta, reverse=True)[:3]
        downs = sorted([m for m in movers if m[1].delta < 0],
                       key=lambda m: m[1].delta)[:3]

        cu, cd = st.columns(2)
        with cu:
            st.markdown("**📈 가장 크게 상승 Top3**")
            for ind, s in ups:
                _mover_line(ind, s, threshold)
            if not ups:
                st.caption("뚜렷한 상승(인접연도) 항목 없음")
        with cd:
            st.markdown("**📉 가장 크게 하락 Top3**")
            for ind, s in downs:
                _mover_line(ind, s, threshold)
            if not downs:
                st.caption("뚜렷한 하락(인접연도) 항목 없음")

        st.divider()
        _render_drivers_barriers(full_inds, [t for t in query.lower().split() if t.strip()], rows)
        _render_inflection_context(inds, query)

        # 신호가 하나도 없으면(모두 비인접/보합) 최신값만이라도 알려준다.
        if not movers:
            st.markdown("**현재 성적표 (최신값)**")
            for ind in inds[:5]:
                s = _summary_headline_series(ind)
                st.write(f"· {ind.label} · {s.label}: **{s.latest.value}{s.unit}** ({s.latest.year})")


def _render_core_indicators(all_inds, threshold):
    """ '핵심 정책 지표' 탭: PRIORITY_GROUPS 의 지표를 추이 카드로. YoY 토글과 무관하게
        전체(추세가능) 지표에서 찾아 항상 보여준다. """
    by_id = {ind.std_id: ind for ind in all_inds}
    st.caption("정책 성과의 핵심 지표를 연도별 추이로 모았습니다. "
               "데이터에 실제 연결된 시계열만 그립니다(없는 지표는 추측하지 않습니다).")
    missing: list[str] = []
    for title, ids in PRIORITY_GROUPS:
        present = [by_id[i] for i in ids if i in by_id]
        missing += [i for i in ids if i not in by_id]
        if not present:
            continue
        st.markdown(f"#### {title}")
        for ind in present:
            _render_indicator_card(ind, threshold)
    if missing:
        st.caption("ℹ️ 현재 정형 데이터에 시계열로 잡히지 않는 지표: " + ", ".join(missing))
    st.caption("ℹ️ '제도별 정인지율(정의를 정확히 아는 비중)'은 현재 데이터에 단독 지표로 "
               "없어 표시하지 않습니다 — 추측해 그리지 않습니다.")


# -----------------------------------------------------------------------------
# 2단계 시각화: 구성(누적막대)·구성(히트맵)·우선순위(파레토)
#   공통 원칙(추측 금지): 연도별로 라벨 표기가 흔들리는(드리프트) 항목이 많아,
#   '여러 해에 걸쳐 같은 라벨로 잡힌 것'만 시계열 비교에 쓴다. 드물게 나온 라벨은
#   잇지 않고 빼며(몇 개 뺐는지 캡션으로 밝힘), 단일연도 스냅샷(파레토)만 원본을 쓴다.
# -----------------------------------------------------------------------------
def _consistent_series(ind, max_items: int = 12, anchor: str = "span"):
    """ 한 지표에서 '여러 해에 걸쳐 안정적으로 같은 라벨로 등장한' 시계열만 추린다.
        anchor="span"(기본): 전체 연도폭 기준(전체-1년 이상 등장). 초기 드리프트 연도가 있으면
          보수적으로 아무것도 안 남길 수 있다 — 인지 경로 히트맵처럼 가짜 연결을 강히 막을 때.
        anchor="block": 기준을 '가장 넓게 잡힌 라벨의 연도 수'로 잡아, 초기 몇 해의 라벨 표기
          드리프트(예: '…를 보고'→'…')가 최근 안정 구간의 연결을 끊지 않게 한다. 이때 표시
          연도는 유지 라벨이 2개 이상 걸친 해만 남겨 외톨이 해(드리프트 1개만 남는 해)를 뺀다.
        반환: (시계열들, 표시연도들, 제외한 라벨 수). 라벨 드리프트로 인한 가짜 연결 방지. """
    from collections import Counter

    all_years = sorted({p.year for s in ind.series for p in s.points})
    if len(all_years) < 2:
        return [], all_years, len(ind.series)
    cover = [len({p.year for p in s.points}) for s in ind.series]
    if anchor == "block":
        need = max(2, (max(cover) if cover else 0) - 1)
    else:
        need = max(2, len(all_years) - 1)
    kept = [s for s in ind.series if len({p.year for p in s.points}) >= need]
    kept.sort(key=lambda s: s.latest.value, reverse=True)
    kept = kept[:max_items]
    if anchor == "block":
        # 유지 라벨이 2개 이상 걸친 해만(드리프트 라벨 하나만 남는 외톨이 해 제외).
        yc = Counter(p.year for s in kept for p in s.points)
        years = [y for y in all_years if yc[y] >= 2]
    else:
        years = all_years
    dropped = len(ind.series) - len(kept)
    return kept, years, dropped


def _stacked_bar_chart(series_kept, years):
    """ 누적막대: 연도(가로)별로 각 판단 기준의 응답률(%)을 색으로 쌓아 구성 변화를 본다.
        다중응답이라 한 해 막대의 합은 100%를 넘을 수 있다(순위가 아닌 '구성' 비교용). """
    import altair as alt
    import pandas as pd

    recs = [{"year": p.year, "label": s.label, "val": p.value}
            for s in series_kept for p in s.points if p.year in years]
    if not recs:
        return None
    df = pd.DataFrame(recs)
    # 값이 큰 항목이 아래에 쌓이도록(값 내림차순) 누적 순서를 고정한다.
    chart = alt.Chart(df).mark_bar().encode(
        x=alt.X("year:O", title="연도"),
        y=alt.Y("val:Q", stack="zero",
                title="응답률(%) — 다중응답이라 합>100% 가능"),
        color=alt.Color("label:N", title="항목",
                        legend=alt.Legend(orient="right", columns=1)),
        order=alt.Order("val:Q", sort="descending"),
        tooltip=[alt.Tooltip("label:N", title="항목"), alt.Tooltip("year:O", title="연도"),
                 alt.Tooltip("val:Q", title="값(%)")],
    )
    return chart.properties(height=360)


def _heatmap_chart(series_kept, years):
    """ 히트맵: 연도(가로)×항목(세로), 칸 색 진하기 = 값(%). 셀에 수치도 표기.
        올드미디어 감소·뉴미디어 증가처럼 '여러 해의 분포 이동'을 한눈에 보기 좋다. """
    import altair as alt
    import pandas as pd

    recs = [{"year": p.year, "label": s.label, "val": p.value}
            for s in series_kept for p in s.points if p.year in years]
    if not recs:
        return None
    df = pd.DataFrame(recs)
    # 비중 평균이 큰 경로를 위로(세로축 정렬). base 엔 color 가 없어 -color 정렬은 무효이므로
    # 값 평균 기준 정렬을 명시한다(이게 없으면 레이어 차트가 렌더되지 않던 문제 수정).
    y_sort = alt.EncodingSortField(field="val", op="mean", order="descending")
    base = alt.Chart(df).encode(
        x=alt.X("year:O", title="연도"),
        y=alt.Y("label:N", title="인지 경로", sort=y_sort,
                axis=alt.Axis(labelOverlap=False, labelLimit=200)),
    )
    rect = base.mark_rect().encode(
        color=alt.Color("val:Q", title="%", scale=alt.Scale(scheme="greens")),
        tooltip=[alt.Tooltip("label:N", title="경로"), alt.Tooltip("year:O", title="연도"),
                 alt.Tooltip("val:Q", title="값(%)")],
    )
    text = base.mark_text(baseline="middle", fontSize=10).encode(
        text=alt.Text("val:Q", format=".0f"),
        color=alt.condition("datum.val > 25", alt.value("white"), alt.value("black")),
    )
    height = max(220, 32 * df["label"].nunique())
    return (rect + text).properties(height=height)


def _pareto_chart(pairs):
    """ 파레토 차트: 막대(값 내림차순) + 누적 % 꺾은선. '소수의 이유가 대부분을 차지'를
        보여줘 우선순위 결정에 쓴다. 누적 %는 '표시된 응답 값 합' 기준이다. """
    import altair as alt
    import pandas as pd

    pairs = sorted([(lab, v) for lab, v in pairs if v is not None], key=lambda t: -t[1])
    if not pairs:
        return None
    total = sum(v for _, v in pairs) or 1.0
    recs, cum = [], 0.0
    for label, v in pairs:
        cum += v
        recs.append({"label": label, "val": v, "cum": round(cum / total * 100, 1)})
    df = pd.DataFrame(recs)
    order = [r["label"] for r in recs]
    bar = alt.Chart(df).mark_bar(color="#5B8FF9").encode(
        x=alt.X("label:N", sort=order, axis=alt.Axis(labelAngle=-40, labelLimit=240, title=None)),
        y=alt.Y("val:Q", title="%"),
        tooltip=[alt.Tooltip("label:N", title="이유"), alt.Tooltip("val:Q", title="값(%)"),
                 alt.Tooltip("cum:Q", title="누적 %")],
    )
    line = alt.Chart(df).mark_line(point=True, color="#E8684A").encode(
        x=alt.X("label:N", sort=order),
        y=alt.Y("cum:Q", title="누적 %", scale=alt.Scale(domain=[0, 100])),
    )
    return alt.layer(bar, line).resolve_scale(y="independent").properties(height=360)


def _inds_by_substr(all_inds, needle):
    """ std_id 에 needle 이 든 지표들(시계열 비교 가능한 것; |Δ| 큰 순 정렬 유지). """
    return [i for i in all_inds if needle in i.std_id]


# 구매 장벽 문항은 별도 '구매 장벽' 카테고리가 아니라 문항명에 '구매하지 않는 이유' 등으로 들어 있다.
# (예: '친환경제품을 구매하지 않는 이유', '환경성적표지 로고 제품을 우선 구매하지 않는 이유')
BARRIER_NEEDLES = ("구매하지 않는 이유", "저해", "구매 장벽")
# 판단 기준 문항도 std_id 가 아니라 문항명에 '판단 기준'으로 들어 있다(단일 연도만 조사된 해도 있음).
JUDGMENT_NEEDLES = ("판단 기준", "판단기준")


def _raw_indicators_by_label(rows, needles):
    """ 원본 행에서 std_label 에 needles 중 하나라도 든 지표의 (std_id, label, 연도들).
        단일연도 항목도 포함(파레토는 한 해 스냅샷). """
    out: dict[str, dict] = {}
    for r in rows:
        lab = (r.get("std_label") or "").strip()
        if not any(n in lab for n in needles):
            continue
        if not (r.get("value") or "").strip():
            continue
        sid = (r.get("std_id") or "").strip()
        if not sid:
            continue
        d = out.setdefault(sid, {"label": lab or sid, "years": set()})
        y = (r.get("year") or "").strip()
        if y:
            d["years"].add(y)
    return [(sid, d["label"], sorted(d["years"])) for sid, d in out.items()]


def _raw_pairs(rows, std_id, year):
    """ (std_id, year) 한 해의 (응답라벨, 값%) 목록. 파레토용 단일연도 스냅샷. """
    pairs = []
    for r in rows:
        if (r.get("std_id") or "").strip() != std_id or (r.get("year") or "").strip() != year:
            continue
        label = (r.get("std_response_label") or r.get("response_label") or "").strip()
        raw = (r.get("value") or "").strip()
        try:
            v = float(raw)
        except ValueError:
            continue
        if label:
            pairs.append((label, v))
    return pairs


# 질문/키워드 필터: 입력한 단어가 든 지표만 모든 탭에서 보이게 한다(LLM 없이 텍스트 매칭).
def _ind_haystack(ind):
    """ 한 지표의 검색 대상 텍스트(지표명·카테고리·요약·응답 라벨)를 소문자로 합쳐 돌려준다. """
    return " ".join([ind.label, ind.category, ind.summary]
                    + [s.label for s in ind.series]).lower()


def _match_terms(haystack, terms):
    """ 입력어 모두(AND)가 텍스트에 들어 있으면 True. terms 가 비면 항상 True(전체 표시). """
    return all(t in haystack for t in terms)


def _filter_inds(inds, terms):
    """ 입력어로 지표 목록을 좁힌다(terms 비면 그대로). """
    if not terms:
        return inds
    return [i for i in inds if _match_terms(_ind_haystack(i), terms)]


def _render_single_year_snapshot(rows, needles, title: str) -> None:
    """ 다년 추세가 없는(그 문항이 한 해만 조사된) 항목을 그 해 스냅샷(빈도 내림차순 막대+누적%)으로
        보여준다. 실제 있는 값만(추측 없음) — 단일 연도라 '추세'가 아니라 그 해 구성만 보여줌을 명시. """
    cands = _raw_indicators_by_label(rows, needles)
    if not cands:
        st.info(f"{title} 데이터가 없습니다.")
        return
    cands.sort(key=lambda t: (-len(t[2]), t[1]))
    idx = st.selectbox("지표", range(len(cands)), key=f"snap_pick_{title}",
                       format_func=lambda i: f"{cands[i][1]}  ({cands[i][2][-1] if cands[i][2] else '-'})")
    sid, lab, yrs = cands[idx]
    year = st.selectbox("연도", list(reversed(yrs)), key=f"snap_year_{title}")   # 최신 연도 기본
    pairs = _raw_pairs(rows, sid, year)
    chart = _pareto_chart(pairs)
    if chart is None:
        st.info("선택한 연도에 표시할 값이 없습니다.")
        return
    st.caption(f"📅 {year}년 스냅샷 — 이 문항은 단일 연도만 조사돼 추세가 아니라 그 해 구성만 보여줍니다.")
    st.altair_chart(chart, width="stretch")
    src = next((r.get("source", "") for r in rows
                if (r.get("std_id") or "").strip() == sid and (r.get("year") or "").strip() == year), "")
    st.caption(f"📊 {lab} · {year}년 · 응답 {len(pairs)}개 · [출처: {src}]")


def _render_judgment_tab(all_inds, rows):
    """ '판단 기준' 탭: 친환경제품 판단 기준의 연도별 구성(누적막대). 다년 데이터가 없으면
        그 해 스냅샷(빈도순 막대)으로 대체한다 — 단일 연도만 조사된 해가 있어서. """
    st.caption("연도별로 각 판단 기준의 응답률(%)을 색으로 쌓아 구성 변화를 봅니다. "
               "다중응답이라 한 해 막대의 합은 100%를 넘을 수 있습니다.")
    cands = _inds_by_substr(all_inds, "판단기준")
    if not cands:
        _render_single_year_snapshot(rows, JUDGMENT_NEEDLES, "판단 기준")
        return
    # 기본은 1순위(단일응답) 지표를 위에 두고(복수응답은 뒤로), 그다음 일관 라벨이 많은 순.
    cands.sort(key=lambda i: ("복수응답" in i.std_id,
                              -len(_consistent_series(i, anchor="block")[0])))
    choice = st.selectbox("지표", cands, format_func=lambda i: i.label, key="bump_pick")
    kept, years, dropped = _consistent_series(choice, anchor="block")
    if len(kept) < 2:
        st.warning("이 지표는 연도별 라벨 표기가 일관되지 않아(드리프트) 구성 비교가 어렵습니다. "
                   "다른 지표(예: 복수응답 버전)를 선택해 보세요.")
        return
    chart = _stacked_bar_chart(kept, years)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
    st.caption(f"📅 {years[0]}~{years[-1]} · 일관 라벨 {len(kept)}개로 비교"
               + (f" (라벨 표기가 흔들리는 {dropped}개는 제외 — 추측 연결 안 함)" if dropped else ""))
    head = kept[0]
    st.caption(f"[출처: {head.source} p.{head.page}]")


def _render_channel_tab(all_inds):
    """ '인지 경로' 탭: 연도×경로 히트맵(올드/뉴미디어 변화). """
    st.caption("정보를 어떤 경로로 접했는지를 연도×경로 히트맵으로 봅니다. "
               "칸이 진할수록 비중이 높습니다(올드미디어 감소·뉴미디어 증가 대비에 적합).")
    cands = _inds_by_substr(all_inds, "인지경로")
    if not cands:
        st.info("인지 경로 시계열 데이터가 없습니다.")
        return
    # 일관 라벨이 많은(=히트맵이 잘 그려지는) 지표를 기본으로 위에 둔다.
    cands.sort(key=lambda i: len(_consistent_series(i)[0]), reverse=True)
    choice = st.selectbox("지표", cands, format_func=lambda i: i.label, key="heat_pick")
    kept, years, dropped = _consistent_series(choice)
    if len(kept) < 2:
        st.warning("이 지표는 연도별 경로 표기가 일관되지 않아(드리프트) 히트맵 비교가 어렵습니다. "
                   "다른 지표를 선택해 보세요.")
        return
    chart = _heatmap_chart(kept, years)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
    st.caption(f"📅 {years[0]}~{years[-1]} · 일관 경로 {len(kept)}개"
               + (f" (표기가 흔들리는 {dropped}개는 제외)" if dropped else ""))
    head = kept[0]
    st.caption(f"[출처: {head.source} p.{head.page}]")


def _render_barrier_tab(rows, terms):
    """ '구매 장벽' 탭: 선택 연도의 장애 요인을 파레토 차트로(빈도순 + 누적%). """
    st.caption("구매를 주저하는 이유를 빈도 내림차순 막대 + 누적 % 꺾은선(파레토)으로 봅니다. "
               "어떤 이유부터 해결하면 효과가 큰지 우선순위를 보여줍니다.")
    cands = _raw_indicators_by_label(rows, BARRIER_NEEDLES)
    if not cands:
        st.info("구매 장벽 데이터가 없습니다.")
        return
    if terms:
        # 입력어로 좁히기: 지표명 + 그 지표의 응답 라벨(이유)까지 포함해 매칭한다.
        cand_sids = {sid for sid, _, _ in cands}
        hay: dict[str, str] = {}
        for r in rows:
            sid = (r.get("std_id") or "").strip()
            if sid not in cand_sids:
                continue
            lab = (r.get("std_response_label") or r.get("response_label") or "").strip()
            hay[sid] = hay.get(sid, "") + " " + lab.lower()
        cands = [(sid, lab, yrs) for sid, lab, yrs in cands
                 if _match_terms((lab.lower() + hay.get(sid, "")), terms)]
        if not cands:
            st.info("입력한 질문에 해당하는 구매 장벽 항목이 없습니다. 입력어를 바꾸거나 비워 보세요.")
            return
    cands.sort(key=lambda t: (-len(t[2]), t[1]))   # 연도 많은 지표를 위로
    labels = [f"{lab}  ({yrs[0]}~{yrs[-1]})" if len(yrs) > 1 else f"{lab}  ({yrs[0] if yrs else '-'})"
              for _, lab, yrs in cands]
    idx = st.selectbox("지표", range(len(cands)), format_func=lambda i: labels[i], key="pareto_pick")
    sid, lab, yrs = cands[idx]
    year = st.selectbox("연도", list(reversed(yrs)), key="pareto_year")   # 최신 연도 기본
    pairs = _raw_pairs(rows, sid, year)
    chart = _pareto_chart(pairs)
    if chart is None:
        st.info("선택한 연도에 표시할 값이 없습니다.")
        return
    st.altair_chart(chart, width="stretch")
    src = next((r.get("source", "") for r in rows
                if (r.get("std_id") or "").strip() == sid and (r.get("year") or "").strip() == year), "")
    st.caption(f"📊 {lab} · {year}년 · 응답 {len(pairs)}개 · [출처: {src}]")


def _mover_card(ind, s, threshold, *, verify: bool = False) -> None:
    """ 추세 카드 한 장(테두리) — 지표명은 전체로(잘림 없이) 윗줄에, 응답라벨·값·Δ 는 metric 으로.
        verify=True 면 '검증 필요'라 방향 색을 끈다(크기가 이례적이라 방향을 단정하지 않음). """
    sig = s.signal(threshold)
    with st.container(border=True):
        st.markdown(f"{signals.SIGNAL_EMOJI[sig]} **{ind.label}**")
        st.markdown(s.label)   # 응답 라벨 — 줄바꿈되어 잘리지 않는다(metric label 은 한 줄이라 잘림)
        st.metric(
            label=s.label, label_visibility="collapsed",
            value=f"{s.latest.value}{s.unit}",
            delta=f"{s.delta:+}%p ({s.prev.year}→{s.latest.year})",
            delta_color="off" if (verify or sig == "flat") else "normal",
        )
        st.caption(f"[출처: {s.source} p.{s.page}]")


def render_step_signal(ctx: dict) -> None:
    """ 🚦 실시간 신호등(랜딩). 정형 데이터의 응답 항목을 연도별로 이어 추세 신호로 표시. """

    st.subheader("🚦 실시간 신호등 (연도별 추세)")
    st.caption(
        "정형 데이터의 응답 항목을 연도별로 이어 추세를 신호로 보여줍니다. "
        "색은 **추세 방향**입니다 — 🟢 상승 · 🟡 보합 · 🔴 하락 "
        "(좋음/나쁨 같은 가치판단이 아닙니다). 데이터에 실제 있는 값·출처만 씁니다."
    )

    try:
        rows = chunking.load_rows()
    except Exception as error:
        st.warning(f"아직 정형 데이터가 없습니다. 🛠 데이터 준비에서 인제스트를 실행해 주세요. ({error})")
        if st.button("🛠 데이터 준비로 이동", key="goto_prep_from_signal"):
            st.session_state.mode = "prep"
            st.rerun()
        return

    ds_years = signals.dataset_years(rows)

    # 질문/키워드 입력 — 입력하면 모든 탭이 '그 질문에 해당하는 항목'만 보이도록 좁힌다.
    query = st.text_input(
        "🔎 질문/키워드로 항목 좁히기",
        placeholder="예: 환경표지 인지도 · 그린카드 · 가격 · 관심도 …  (비우면 전체 표시)",
        help="입력한 단어가 들어간 지표만 모든 탭에서 보여줍니다(지표명·카테고리·요약·응답 라벨 대상). "
             "공백으로 여러 단어를 넣으면 모두 포함하는 항목만 남습니다. LLM 없이 데이터 텍스트만 매칭합니다.")
    terms = [t.lower() for t in query.split() if t.strip()]

    c_th, c_cov = st.columns([3, 2])
    with c_th:
        threshold = st.slider("신호 임계값 (이 %p 이상 변하면 상승/하락)",
                              1.0, 10.0, signals.DEFAULT_THRESHOLD_PP, 0.5)
    with c_cov:
        recent_yoy_only = st.checkbox(
            "최근 연속년(YoY) 항목만", value=False,
            help="끄면 2개년 이상 모든 추세 항목을 봅니다(기본). 켜면 가장 최근 두 "
                 "연도가 인접(전년대비 비교 가능)한 항목만 봅니다. 신호(🟢🟡🔴)는 "
                 "어느 모드든 인접 연도일 때만 매겨, 끊긴 구간의 가짜 큰 변동은 신호로 잡지 않습니다.")

    full_inds = signals.compute_signals(rows, threshold_pp=threshold, min_coverage=2)
    all_inds = _filter_inds(full_inds, terms)   # 입력한 질문에 해당하는 지표만 남긴다
    if recent_yoy_only:
        inds = [i for i in all_inds if any(s.is_yoy for s in i.series)]
    else:
        inds = all_inds
    st.caption(f"📅 데이터 연도: {'·'.join(map(str, ds_years))} · "
               f"{len(inds)}문항 표시"
               + (f" · 🔎 '{query}' 필터" if terms else "")
               + (f" (전체 추세가능 {len(all_inds)}문항 중 최근 연속년만)"
                  if recent_yoy_only else " (2개년 이상 전체 추세)"))
    if not inds:
        if terms:
            st.info(f"🔎 '{query}'에 해당하는 추세 항목이 없습니다. 입력어를 바꾸거나 비워 전체를 보세요.")
        else:
            st.info("표시할 추세 데이터가 없습니다. '최근 연속년(YoY) 항목만'을 끄거나 "
                    "여러 연도의 PDF를 인제스트해 보세요.")
        return

    # 검색어가 있으면 '결론 먼저': 상단에 근거 있는 핵심 요약(키워드 요약·신호 집계·
    # 최대 상승/하락 Top3)을 보여주고, 아래 탭은 '상세 근거'로 둔다.
    if terms:
        _render_query_summary(inds, threshold, query, ds_years, full_inds, rows)
        st.divider()
        st.markdown("### 📊 상세 근거 (연도별 추이)")

    tab_trend, tab_core, tab_judge, tab_chan, tab_barrier = st.tabs(
        ["🚦 추세 신호", "📊 핵심 정책 지표", "🧭 판단 기준", "📡 인지 경로", "🚧 구매 장벽"])

    with tab_trend:
        # 의사결정 링크: 이 데이터로 2026 설문 설계 제언(RAG advise)으로 바로 이동.
        if st.button("💡 이 데이터로 2026 설문 설계 제언 받기", type="primary"):
            st.session_state["rag_mode"] = "데이터 기반 제언"
            st.session_state["rag_question"] = "최근 3개년 근거로 2026 인지도 설문을 어떻게 설계하면 좋을까?"
            st.session_state["mode"] = "qa"
            st.rerun()

        counts = signals.summarize(inds, threshold)
        c1, c2, c3 = st.columns(3)
        c1.metric("🟢 상승", counts["up"])
        c2.metric("🟡 보합", counts["flat"])
        c3.metric("🔴 하락", counts["down"])
        st.caption("전체 방향 신호 수(집계·비응답·거울상 중복·문서상 척도 변경 제외). "
                   "아래에서 '설계 동일·크기 정상'만 헤드라인하고, 이례적 급변·개편 구간은 분리합니다.")

        # 의사결정 3단 분리로 오독을 막는다:
        #   🟢 헤드라인 = 설계 동일 구간(2024→2025) + 단년 변동 크기가 평범(|Δ|≤LARGE) → 바로 판단.
        #   🔶 검증 필요 = 설계 동일이나 |Δ| 가 이례적으로 큼 → 설문 변경 가능성, 원문 확인 후 판단.
        #   ⚠️ 해석 유의 = 2023→2024 개편 구간 + 문서상 척도 변경 → 실제 변화 아닐 수 있음.
        movers = signals.signaled_movers(inds, threshold)
        recent = [(i, s) for i, s in movers if not s.spans_scale_break]
        plausible = [(i, s) for i, s in recent if abs(s.delta) <= signals.LARGE_YOY_PP]
        oversized = [(i, s) for i, s in recent if abs(s.delta) > signals.LARGE_YOY_PP]
        redesign = [(i, s) for i, s in movers if s.spans_scale_break]
        caveated = signals.caveat_breaks(inds)

        st.markdown("#### 📊 주목할 실제 변화 — 설계 동일 · 크기 정상 (바로 판단 가능)")
        if plausible:
            cols = st.columns(3)
            for i, (ind, s) in enumerate(plausible[:9]):
                with cols[i % 3]:
                    _mover_card(ind, s, threshold)
            if len(plausible) > 9:
                st.caption(f"… 외 {len(plausible) - 9}개(변화 작은 순). 카테고리별 추세에서 더 보세요.")
        else:
            st.caption("설계가 같고 크기가 평범한(신뢰할 만한) 뚜렷한 변화가 없습니다. 임계값을 낮춰 보세요.")

        if oversized:
            with st.expander(f"🔶 큰 변화지만 검증 필요 — 단년 변동이 이례적으로 큼(>{signals.LARGE_YOY_PP:.0f}%p) "
                             f"{len(oversized)}건"):
                st.caption(f"설문 설계는 같은 구간이지만 한 해에 {signals.LARGE_YOY_PP:.0f}%p 넘게 변했습니다. "
                           "인지도 조사의 단년 변동 중앙값은 6.6%p로, 이만큼 큰 변화는 보기 개편 등 설문 "
                           "변경일 수 있어 **원문(출처 페이지)을 확인한 뒤** 판단하세요(실제 변화가 아니라고 "
                           "단정하지는 않습니다).")
                cols = st.columns(3)
                for i, (ind, s) in enumerate(oversized[:9]):
                    with cols[i % 3]:
                        _mover_card(ind, s, threshold, verify=True)

        n_cau = len(redesign) + len(caveated)
        if n_cau:
            with st.expander(f"⚠️ 해석 유의 — 2023→2024 설문 개편 구간·척도 변경 {n_cau}건 "
                             "(실제 변화가 아닐 수 있어 헤드라인에서 분리)"):
                st.caption("2024 조사에서 다수 문항이 개편돼 2023→2024 변화는 설문 변경 영향일 수 "
                           "있습니다(데이터상 2023→2024 급변이 2024→2025의 약 4배). 문서로 확인된 "
                           "척도 변경은 ⚠️로 표시하며, 원값은 참고로만 보세요.")
                for ind, s in caveated[:8]:
                    st.markdown(f"⚠️ **{ind.label}** · {s.label}: {s.latest.value}{s.unit} "
                                f"(원값 {s.delta:+}%p, {s.prev.year}→{s.latest.year} · **문서상 척도 변경**)  \n"
                                f"[출처: {s.source} p.{s.page}]")
                for ind, s in redesign[:12]:
                    st.markdown(f"· **{ind.label}** · {s.label}: {s.latest.value}{s.unit} "
                                f"(원값 {s.delta:+}%p, {s.prev.year}→{s.latest.year} · 개편 구간)  \n"
                                f"[출처: {s.source} p.{s.page}]")

        st.divider()
        # 카테고리별 추세 — 선택한 카테고리에서 변화 큰 지표 위주로 차트.
        st.markdown("#### 카테고리별 추세")
        cats = signals.categories(inds)
        cat = st.selectbox("카테고리", cats)
        in_cat = [i for i in inds if i.category == cat]   # compute_signals 가 |Δ| 큰 순으로 정렬됨
        LIMIT = 10
        for ind in in_cat[:LIMIT]:
            _render_indicator_card(ind, threshold)
        if len(in_cat) > LIMIT:
            st.caption(f"… 외 {len(in_cat) - LIMIT}개 지표는 변화가 작아 생략했습니다 "
                       f"(임계값을 낮추거나 다른 카테고리에서 확인하세요).")

    with tab_core:
        _render_core_indicators(all_inds, threshold)

    with tab_judge:
        _render_judgment_tab(all_inds, rows)

    with tab_chan:
        _render_channel_tab(all_inds)

    with tab_barrier:
        _render_barrier_tab(rows, terms)
