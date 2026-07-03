# ui/rag.py
# -----------------------------------------------------------------------------
# 💬 질의(Q&A) 모드 — 정형 데이터에서 검색해 출처 인용 답변. '데이터 기반 제언'
# 모드와 답변 상세도(요약/표준/상세)를 함께 제공한다(답변 로직은 rag/retrieval/answer.py).
# -----------------------------------------------------------------------------
from __future__ import annotations

import re

import streamlit as st

from ui.common import DATA_DIR


# 6.7 예시 질문 — 세션당 1회만 생성해 캐시(매 rerun LLM 재호출 방지). 실데이터 기반.
@st.cache_data(show_spinner=False)
def _cached_examples() -> list[str]:
    from rag.retrieval.answer import suggest_questions
    return suggest_questions(4)


# 연도 옵션 — 매 rerun 전체 CSV 재파싱을 피하려고 소스 mtime 을 캐시 키로 쓴다
# (재인제스트로 파일이 바뀌면 자동 갱신).
@st.cache_data(show_spinner=False, max_entries=2)
def _year_options(mtime: float) -> list[str]:
    from rag.retrieval import chunking
    from rag import signals
    return ["전체"] + [str(y) for y in signals.dataset_years(chunking.load_rows())]


def _first_page(page_str: str) -> int | None:
    """ '103-105' 같은 페이지 표기에서 첫 페이지 번호만 뽑는다(없으면 None). """
    m = re.match(r"\s*(\d+)", page_str or "")
    return int(m.group(1)) if m else None


# 원문 페이지 미리보기(온디맨드) — extract_vision 렌더러 재사용, (source, page) 단위 캐시.
@st.cache_data(show_spinner=False, max_entries=32)
def _page_png(source: str, page: int, dpi: int = 120) -> bytes | None:
    """ 원문 PDF 페이지를 PNG 로 렌더링. PDF 가 없으면 None(샘플 클론엔 PDF 없음). """
    from rag.ingest.extract_vision import render_page_images, _resolve_pdf
    try:
        pdf = _resolve_pdf(source)
    except FileNotFoundError:
        return None
    imgs = render_page_images(pdf, page, page, dpi=dpi)
    return imgs[0] if imgs else None


# advise 갈래별 배지(색)와 한 줄 요지 — 내용은 LLM 원문 그대로, 표시만 카드로 구조화.
_ADVISE_BADGE = {
    "KEEP": (":green-badge[KEEP · 유지]", "추세가 뚜렷해 유지할 문항"),
    "ADD":  (":blue-badge[ADD · 신설]", "데이터 공백 — 새로 물을 것"),
    "DROP": (":red-badge[DROP · 축소]", "변별력 낮음 — 줄이거나 뺄 것"),
    "FIX":  (":violet-badge[FIX · 설계 교정]", "척도·정의 표준화 대상"),
}


def _render_advise(sections) -> None:
    """ advise 답변(파싱 성공분)을 갈래별 카드로 보여준다 — 분할만 하고 내용은 원문 그대로. """
    if sections.preamble:
        st.markdown(sections.preamble)
    # 존재하는 갈래만 배지 요약 행(한눈에 어떤 제언이 있는지)
    st.markdown(" ".join(_ADVISE_BADGE[k][0] for k, _, _ in sections.advice
                         if k in _ADVISE_BADGE))
    for kind, _head, body in sections.advice:
        badge, hint = _ADVISE_BADGE.get(kind, ("", ""))
        with st.container(border=True):
            st.markdown(badge)
            if hint:
                st.caption(hint)
            st.markdown(body or "_(데이터로 판단 불가)_")
    if sections.facts:
        with st.expander("📊 근거 사실 보기", expanded=False):
            st.markdown(sections.facts)


# -----------------------------------------------------------------------------
# 질의(Q&A) 화면 — 정형 데이터에서 검색 + 근거 인용 답변
# -----------------------------------------------------------------------------
def render_rag_tab(gate=None):
    st.subheader("🔎 데이터 질의 (RAG · 근거 인용)")
    st.caption(
        "정형화된 인지도 조사 데이터에서 검색해 **출처(파일·페이지)를 인용**해 답합니다. "
        "근거가 없으면 '문서에서 찾을 수 없습니다'라고 답합니다."
    )
    # D5: 인덱스가 게이트 미통과 데이터로 만들어졌으면 답변 신뢰에 주의.
    if gate is not None and not gate.ok:
        st.warning(
            "⚠️ 현재 인덱스는 **게이트 미통과 데이터**(미확정 비전·빈값·미검수 high 포함 가능)로 "
            "만들어졌을 수 있습니다. 3단계 검수로 확정 후 4단계에서 재인덱싱하면 답변이 더 정확해집니다."
        )

    # key 로 세션 주입을 허용한다(신호등 탭의 '2026 제언 받기' 버튼이 모드·질문을 채움).
    mode_label = st.radio(
        "답변 방식", ["사실 인용", "데이터 기반 제언"], horizontal=True, key="rag_mode",
        help="'사실 인용' = 데이터에 있는 사실만 출처와 함께. "
             "'데이터 기반 제언' = 3개년 추세를 근거로 권장(📊 근거 사실은 인용, 💡 제언은 추론으로 분리 표기).",
    )
    mode = "advise" if mode_label == "데이터 기반 제언" else "cite"
    detail = st.radio(
        "답변 상세도", ["요약", "표준", "상세"], index=1, horizontal=True,
        help="같은 근거로 서술 길이·깊이만 조절합니다. "
             "'요약'=핵심만 1~2줄, '표준'=기본, '상세'=구체 수치·함의·한계까지.",
    )
    rewrite = st.checkbox(
        "🔎 질문 재작성(recall↑)", value=False,
        help="짧거나 구어체인 질문을 검색에 유리하게 정규화·확장해 관련 근거를 더 잘 찾습니다(6.4). "
             "연도·표 판단과 화면 표시는 원 질문을 그대로 씁니다.",
    )

    # 6.7 예시 질문 — 클릭하면 질문칸을 채운다(질문이 비었을 때만 노출).
    if not st.session_state.get("rag_question"):
        examples = _cached_examples()
        if examples:
            st.caption("💡 예시 질문 — 클릭하면 채워집니다")
            for i, ex in enumerate(examples):
                if st.button(ex, key=f"rag_ex_{i}", width="stretch"):
                    st.session_state["rag_question"] = ex
                    st.rerun()

    ph = ("예: 3개년 추세로 보아 2026 설문은 어떻게 설계하는 게 좋을까?"
          if mode == "advise" else "예: 2024년 환경표지 정의 인지율은?")
    c1, c2 = st.columns([3, 1])
    with c2:
        # 연도 필터는 데이터에서 도출(하드코딩 금지) — 새 연도 보고서를 올리면 자동 반영.
        try:
            from rag.retrieval import chunking
            year_opts = _year_options(chunking.SOURCE_CSV.stat().st_mtime)
        except Exception:
            year_opts = ["전체"]   # 정형 데이터가 아직 없으면 필터 없이 진행
        year = st.selectbox("연도 필터", year_opts, index=0)
    with c1:
        question = st.text_input("질문", placeholder=ph, key="rag_question")

    if not question:
        return

    try:
        from rag.retrieval.answer import answer as rag_answer
    except Exception as error:
        st.error(f"RAG 모듈을 불러오지 못했습니다: {error}")
        return

    with st.spinner("검색하고 근거로 답하는 중..."):
        try:
            result = rag_answer(question, k=5, year=None if year == "전체" else year,
                                mode=mode, detail=detail, rewrite=rewrite)
        except Exception as error:
            st.error(
                f"검색/답변 중 오류: {error}\n\n"
                "인덱스가 없다면 4단계(📚 인덱싱)에서 '📚 인덱싱 실행'을 눌러 주세요."
            )
            return

    if result.rewritten:
        st.caption(f"🔎 검색어 재작성: _{result.rewritten}_")

    # advise 모드는 헤딩 계약(#### KEEP…)이 지켜졌을 때만 갈래별 카드로 구조화한다.
    # 파싱이 어긋나면 원문 마크다운 그대로 — LLM 이 안 쓴 구조를 합성하지 않는다.
    rendered = False
    if mode == "advise":
        from rag.retrieval.answer import parse_advise_sections
        sections = parse_advise_sections(result.text)
        if sections is not None:
            _render_advise(sections)
            rendered = True
    if not rendered:
        st.markdown(result.text)

    # 왜 느린지 가시화 — 단계별 소요시간(검색 vs 답변 생성)
    tm = result.timings or {}
    st.caption(
        f"⏱ 처리 시간 — 검색 {tm.get('retrieval', 0)}s · 답변 생성 {tm.get('generate', 0)}s "
        f"· 합계 **{tm.get('total', 0)}s** (대부분 LLM 답변 생성에서 소요)"
    )

    with st.expander(f"📎 근거 출처 {len(result.hits)}건 보기"):
        for i, h in enumerate(result.hits, start=1):
            with st.container(border=True):
                st.markdown(
                    f"**[{i}]** :blue-badge[{h.metadata.get('year', '?')}년] "
                    f":gray-badge[{h.metadata.get('std_id', '')}] — **{h.locator}**"
                )
                st.caption(f"유사도 {h.score} · " + h.text.replace("\n", " ")[:200] + " ...")
                # 원문 페이지 미리보기 — PDF 가 실제로 있고 페이지 번호가 있을 때만 토글 노출
                # (없는데 보여주면 죽은 UI). 켰을 때만 렌더(온디맨드 + 캐시)해 비용 0에 가깝게.
                src = h.metadata.get("source", "")
                page_no = _first_page(h.metadata.get("page", ""))
                if src and page_no and (DATA_DIR / src).exists():
                    if st.toggle("📄 원문 페이지 보기", key=f"rag_src_pg_{h.chunk_id}"):
                        png = _page_png(src, page_no)
                        if png:
                            st.image(png, width="stretch", caption=f"{src} p.{page_no}")
