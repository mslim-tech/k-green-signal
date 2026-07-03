# ui/rag.py
# -----------------------------------------------------------------------------
# 💬 질의(Q&A) 모드 — 정형 데이터에서 검색해 출처 인용 답변. '데이터 기반 제언'
# 모드와 답변 상세도(요약/표준/상세)를 함께 제공한다(답변 로직은 rag/retrieval/answer.py).
# -----------------------------------------------------------------------------
from __future__ import annotations

import streamlit as st


# 6.7 예시 질문 — 세션당 1회만 생성해 캐시(매 rerun LLM 재호출 방지). 실데이터 기반.
@st.cache_data(show_spinner=False)
def _cached_examples() -> list[str]:
    from rag.retrieval.answer import suggest_questions
    return suggest_questions(4)


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
                if st.button(ex, key=f"rag_ex_{i}", use_container_width=True):
                    st.session_state["rag_question"] = ex
                    st.rerun()

    ph = ("예: 3개년 추세로 보아 2026 설문은 어떻게 설계하는 게 좋을까?"
          if mode == "advise" else "예: 2024년 환경표지 정의 인지율은?")
    c1, c2 = st.columns([3, 1])
    with c2:
        # 연도 필터는 데이터에서 도출(하드코딩 금지) — 새 연도 보고서를 올리면 자동 반영.
        try:
            from rag.retrieval import chunking
            from rag import signals
            year_opts = ["전체"] + [str(y) for y in signals.dataset_years(chunking.load_rows())]
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
    st.markdown(result.text)

    # 왜 느린지 가시화 — 단계별 소요시간(검색 vs 답변 생성)
    tm = result.timings or {}
    st.caption(
        f"⏱ 처리 시간 — 검색 {tm.get('retrieval', 0)}s · 답변 생성 {tm.get('generate', 0)}s "
        f"· 합계 **{tm.get('total', 0)}s** (대부분 LLM 답변 생성에서 소요)"
    )

    with st.expander(f"📎 근거 출처 {len(result.hits)}건 보기"):
        for i, h in enumerate(result.hits, start=1):
            st.markdown(
                f"**[{i}]** {h.metadata.get('year')}년 · `{h.metadata.get('std_id')}` "
                f"— {h.locator} (유사도 {h.score})"
            )
            st.caption(h.text.replace("\n", " ")[:200] + " ...")
