# ui/index.py
# -----------------------------------------------------------------------------
# 인덱싱 화면(4단계) — 준비 게이트를 통과해야 청킹+임베딩(Chroma)을 실행한다.
# (추측은 데이터가 아니다 — 확정 사실만 인덱싱)
# -----------------------------------------------------------------------------
from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger("app")


def render_step_index(ctx: dict, gate=None) -> None:
    st.subheader("📚 4단계 · 인덱싱 (준비 게이트)")
    st.caption("아래 준비 게이트를 통과해야 인덱싱할 수 있습니다. (추측은 데이터가 아니다 — 확정 사실만 인덱싱)")
    rep = gate
    if rep is None:
        try:
            from rag.curate.validate import validate_ready
            rep = validate_ready(strict=True)
        except Exception as error:
            st.error(f"준비 점검 실패: {error}")
            return

    if rep.ok:
        st.success(f"✅ {rep.summary}")
    else:
        st.error(f"⛔ {rep.summary}")
        for c in rep.blocking:
            with st.expander(f"⛔ {c.label}: {c.count}건"):
                for it in c.items:
                    st.write(f"- {it}")
                st.caption(f"↳ {c.fix_hint}")

    # 게이트 통과 시에만 인덱싱 실행(추측은 데이터가 아니다 — 확정 사실만 인덱싱).
    if st.button("📚 인덱싱 실행", disabled=not rep.ok, key="run_index",
                 help=None if rep.ok else "준비 게이트를 통과해야 활성화됩니다."):
        with st.status("인덱싱 중…", expanded=True) as status:
            try:
                from rag.retrieval import chunking
                from rag.retrieval import index as indexmod
                st.write("청킹(확정 사실 + 방법론·외부 맥락 지식청크)…")
                # CLI(chunking.main)와 동일한 전체 청크셋 — 사실 청크만 넣으면
                # advise 모드의 근거(방법론·외부 맥락 지식청크)가 조용히 사라진다.
                chunks = chunking.build_all_chunks(chunking.load_rows())
                chunking.save_chunks(chunks)
                st.write(f"청크 {len(chunks)}개 — 임베딩·Chroma 인덱싱…")
                n = indexmod.build_index(reset=True)
                logger.info("인덱싱 완료: %d 청크", n)
                status.update(label=f"✅ 인덱싱 완료: {n} 청크", state="complete")
            except Exception as error:
                logger.exception("인덱싱 실패")
                status.update(label=f"⛔ 인덱싱 실패: {error}", state="error")
        st.rerun()

    st.write(f"현재 인덱스 청크: {ctx['index_count']}")
