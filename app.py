# app.py
# -----------------------------------------------------------------------------
# RAG Lab - Streamlit 앱 (탭 2개)
#
# 이 파일의 역할:
#   1) "📄 문서 Q&A" 탭 (Phase 1 Baseline, Long Context 방식)
#      - 문서(PDF/TXT/DOCX)를 업로드하면 텍스트를 추출하고, 문서 전체를
#        프롬프트에 넣어 OpenAI 모델에게 답변을 받는다.
#      - 아직 Chunking / Embedding / Vector DB / Retriever 는 사용하지 않는다.
#   2) "🔍 검수" 탭 (5단계)
#      - 4단계 산출 outputs/review_queue.csv(저신뢰 행)을 표로 보여주고,
#        사람이 값을 확인/수정하면 outputs/corrections.jsonl 에 기록한다.
#      - 저장/적용 로직은 rag/corrections.py 가 담당한다(여기선 화면만).
# -----------------------------------------------------------------------------

import csv
import io
import logging
import os
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

import pypdf   # 1단계 업로드에서 페이지 수 표시용

# 검수 기록 저장/적용 로직 (5단계)
from rag import corrections

# 공용 로깅 (파일+콘솔). 앱이 무슨 일을 하는지 logs/ 에 남긴다.
from rag.logging_setup import setup_logging

# 인제스트 단계를 서브프로세스로 돌리는 러너(긴 LLM 단계가 UI 를 막지 않게).
from rag import pipeline

# 실시간 신호등(6단계): 정형 데이터의 연도별 추세 신호 계산.
from rag import chunking, signals

log = logging.getLogger("app")

# 4단계가 만든 검수 큐 파일 위치
REVIEW_QUEUE_PATH = Path("outputs") / "review_queue.csv"


# -----------------------------------------------------------------------------
# 1) 초기 설정: .env 에서 API Key 읽기
#    - 보안 규칙: API Key 는 .env 의 OPENAI_API_KEY 에서만 읽는다.
# -----------------------------------------------------------------------------
def get_api_key():
    """ .env 에서 OPENAI_API_KEY 를 읽어 반환한다. 없으면 None 을 반환한다. """
    load_dotenv()
    return os.getenv("OPENAI_API_KEY")


# -----------------------------------------------------------------------------
# 4) 검수 탭 (5단계)
#    - review_queue.csv 를 표로 보여주고, 행을 고르면 상세 + 수정 폼을 연다.
#    - 저장은 rag/corrections.py 가 outputs/corrections.jsonl 에 한 줄씩 쌓는다.
# -----------------------------------------------------------------------------

# 검수 큐는 한 번 만들어지면 잘 안 바뀌므로 캐시한다(앱이 빨라진다).
@st.cache_data
def load_review_queue():
    """ review_queue.csv 를 dict 리스트로 읽는다. 파일이 없으면 None. """
    if not REVIEW_QUEUE_PATH.exists():
        return None
    with open(REVIEW_QUEUE_PATH, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# 표에 보여줄 핵심 컬럼(31개 다 보여주면 복잡하므로 추렸다). 상세는 아래 패널에서.
TABLE_COLUMNS = [
    "review_priority", "year", "std_id", "std_response_label",
    "value", "unit", "review_reasons", "extraction_confidence", "warning",
]

# 검수 상태를 사람이 읽는 말로.
STATUS_LABELS = {
    corrections.STATUS_FIXED: "값 고침",
    corrections.STATUS_CONFIRMED: "원래 값 맞음",
    corrections.STATUS_SKIP: "보류",
}


def effective_value(row, latest):
    """ 검수 정정이 '반영된' 값을 돌려준다.
        - 'fixed'(값 고침)면 고친 값, 그 외(confirmed/없음)면 원본 값.
        review_queue 표에 '정정값'으로 보여주거나, 수정 폼 기본값으로 쓴다. """
    rec = latest.get(corrections.row_key(row) + ("value",))
    if rec and rec.get("status") == corrections.STATUS_FIXED:
        return rec.get("new_value", "")
    return row.get("value", "")


def needs_value(row, latest) -> bool:
    """ 이 행이 '값이 비어 반드시 검수해야 하는' 행인가.
        반영값(정정 포함)이 숫자로 읽히지 않으면 True.

        단, 사람이 이미 검수해 '빈 값이 맞다(confirmed)'거나 '제외(skip)'로 처리한 행은
        더 이상 검수 대상이 아니다 — 같은 빈칸을 반복해 들이밀지 않는다(사용자 요청). """
    rec = latest.get(corrections.row_key(row) + ("value",))
    if rec and rec.get("status") in (corrections.STATUS_CONFIRMED, corrections.STATUS_SKIP):
        return False
    v = (effective_value(row, latest) or "").strip()
    if not v:
        return True
    try:
        float(v)
        return False
    except ValueError:
        return True


def render_detail_and_edit(row, latest):
    """ 선택한 행의 상세 정보 + 수정 폼을 그린다. """
    st.divider()
    st.markdown(f"### {row.get('std_id')} · {row.get('year')}년")

    left, right = st.columns(2)
    with left:
        st.write(f"**문항 요약:** {row.get('question_summary', '')}")
        st.write(f"**표준 응답 라벨:** {row.get('std_response_label', '')}")
        unit = row.get("unit", "")
        original = row.get("value", "")
        eff = effective_value(row, latest)
        if eff != original:
            # 정정이 반영된 경우: 원본 → 정정값을 한눈에 비교.
            st.write(f"**원본 값:** {original or '(빈값)'} {unit}  →  **정정값:** :green[{eff} {unit}]")
        else:
            st.write(f"**현재 값:** {original or '(빈값)'} {unit}")
        st.write(f"**섹션:** {row.get('section', '')} / {row.get('subsection', '')}")
    with right:
        st.write(f"**우선순위:** {row.get('review_priority', '')}")
        st.write(f"**검수 사유:** {row.get('review_reasons', '')}")
        st.write(f"**출처:** {row.get('source_locator', '')}")
        st.write(f"**추출 신뢰도:** {row.get('extraction_confidence', '')}")

    if (row.get("warning") or "").strip():
        st.warning(f"warning: {row.get('warning')}")

    # 4.3 플래그를 사람이 이해할 수 있게 풀어서 보여준다.
    flag_bits = []
    if row.get("flag_jump") == "True":
        flag_bits.append(f"전년 대비 급변 (이전 {row.get('prev_value')} → 현재 {row.get('value')}, Δ{row.get('yoy_delta')}%p)")
    if row.get("flag_mismatch") == "True":
        flag_bits.append(f"전년 노트와 모순: {row.get('mismatch_reason')}")
    if row.get("flag_sum_violation") == "True":
        flag_bits.append(f"보기 합계 {row.get('sum_total')} (100±5 벗어남)")
    if flag_bits:
        st.info(" · ".join(flag_bits))
    if (row.get("prev_year_note") or "").strip():
        st.caption(f"전년 대비 노트(원문): {row.get('prev_year_note')}")

    # 이 행에 대한 직전 검수 기록이 있으면 보여준다(같은 행 재검수 참고용).
    prev = latest.get(corrections.row_key(row) + ("value",))
    if prev:
        st.caption(
            f"📝 이전 검수: {STATUS_LABELS.get(prev.get('status'), prev.get('status'))}"
            f" / 고친값={prev.get('new_value') or '-'} / 메모={prev.get('note') or '-'}"
            f" ({prev.get('ts')})"
        )

    # --- 수정 폼 ---
    with st.form("review_edit_form"):
        status = st.radio(
            "검수 결과",
            list(STATUS_LABELS.keys()),
            format_func=lambda s: STATUS_LABELS[s],
            horizontal=True,
        )
        # 이미 정정된 값이 있으면 그 값을 기본으로 채워, 확인 후 그대로 저장만 누르면 되게 한다.
        new_value = st.text_input("고친 값 ('값 고침'일 때만 반영)", value=effective_value(row, latest))
        note = st.text_input("메모(선택)", value=(prev.get("note", "") if prev else ""))
        reviewer = st.text_input("검수자(선택)", value=(prev.get("reviewer", "") if prev else ""))
        submitted = st.form_submit_button("💾 저장")
        if submitted:
            corrections.add_correction(
                row,
                status=status,
                # '값 고침'이 아니면 new_value 는 의미가 없으므로 빈 값으로 저장한다.
                new_value=new_value if status == corrections.STATUS_FIXED else "",
                note=note,
                reviewer=reviewer,
            )
            st.success("저장했습니다. (outputs/corrections.jsonl)")
            st.rerun()


def render_review_tab():
    st.subheader("🔍 검수 큐")
    st.caption("4단계가 고른 저신뢰 행을 사람이 확인/수정합니다. 원본 CSV 는 건드리지 않고 corrections.jsonl 에만 기록합니다.")

    queue = load_review_queue()
    if queue is None:
        st.info("`outputs/review_queue.csv` 가 없습니다. 먼저 4단계(`uv run python rag/review.py`)를 실행하세요.")
        return
    if not queue:
        st.success("검수할 행이 없습니다. 🎉")
        return

    # 검수 기록을 매번 새로 읽어 '검수 완료' 표시를 최신으로 유지한다(파일이 작아 빠름).
    recs = corrections.load_corrections()
    reviewed = corrections.reviewed_keys(recs)
    latest = corrections.latest_by_key(recs)

    # --- D3: 값이 비어 반드시 검수해야 하는 행 안내(직접 찾지 않게) ---
    # 이미 사람이 '빈 값이 맞다(confirmed)'거나 '제외(skip)'로 처리한 행은 needs_value 가
    # 빼주므로(반복 노출 방지), 여기 남는 건 '아직 손대지 않은' 빈칸뿐이다.
    blank_rows = [r for r in queue if needs_value(r, latest)]
    if blank_rows:
        st.warning(
            f"⚠️ **값이 비어 검수가 필요한 행: {len(blank_rows)}건** — "
            "아래 '값 없는 행만 보기'로 모아 보고, 행을 골라 값을 채워주세요."
        )
    else:
        st.success(
            "✅ 값 없는 행이 모두 검수 처리되었습니다 "
            "(빈 값이 맞다고 확인했거나 제외한 행은 다시 띄우지 않습니다)."
        )
    only_blank = st.checkbox("값 없는 행만 보기", value=bool(blank_rows),
                             help="반영값이 비었거나 숫자가 아닌 행만 표시합니다.")

    # --- 필터 ---
    years = sorted({(r.get("year") or "") for r in queue})
    reasons_all = sorted({
        x for r in queue
        for x in (r.get("review_reasons") or "").split("; ") if x
    })
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        f_priority = st.multiselect("우선순위", ["high", "medium"], default=["high", "medium"])
    with c2:
        f_year = st.multiselect("연도", years, default=years)
    with c3:
        f_reason = st.multiselect("검수 사유(택1↑ 시 해당 행만)", reasons_all, default=[])
    with c4:
        hide_done = st.checkbox("미검수만 보기", value=False)

    def keep(r):
        if only_blank and not needs_value(r, latest):
            return False
        if r.get("review_priority") not in f_priority:
            return False
        if (r.get("year") or "") not in f_year:
            return False
        if f_reason and not any(x in (r.get("review_reasons") or "") for x in f_reason):
            return False
        if hide_done and corrections.row_key(r) in reviewed:
            return False
        return True

    filtered = [r for r in queue if keep(r)]
    # 값 없는 행을 맨 위로(검수 우선) — 그 안에서는 원래 순서 유지(stable).
    filtered.sort(key=lambda r: 0 if needs_value(r, latest) else 1)
    done_n = sum(1 for r in queue if corrections.row_key(r) in reviewed)
    st.caption(f"표시 {len(filtered)}행 / 전체 {len(queue)}행 · 검수 완료 {done_n}행 · 값없음 {len(blank_rows)}행")

    if not filtered:
        st.info("필터에 해당하는 행이 없습니다.")
        return

    # --- 표 (행 선택 가능) ---
    # 원본 값 옆에 '정정값'(검수 반영값)을 같이 보여줘 수치가 제대로 들어갔는지 눈으로 확인.
    import pandas as pd
    table_rows = []
    for r in filtered:
        row_view = {}
        for c in TABLE_COLUMNS:
            row_view[c] = r.get(c, "")
            if c == "value":
                eff = effective_value(r, latest)
                # 정정으로 값이 바뀐 경우만 따로 표시(같으면 빈칸으로 두어 시선 분산 방지).
                row_view["정정값"] = eff if eff != (r.get("value", "")) else ""
        row_view["확인필요"] = "🔴" if needs_value(r, latest) else ""
        row_view["검수"] = "✅" if corrections.row_key(r) in reviewed else ""
        table_rows.append(row_view)
    df = pd.DataFrame(table_rows)

    event = st.dataframe(
        df,
        selection_mode="single-row",
        on_select="rerun",
        hide_index=True,
        use_container_width=True,
        height=360,
    )
    selected = event.selection.rows
    if not selected:
        st.info("표에서 행을 선택하면 상세 정보와 수정 화면이 열립니다.")
        return

    render_detail_and_edit(filtered[selected[0]], latest)


# -----------------------------------------------------------------------------
# 4b) RAG 데이터 질의 탭 (6단계) — 정형 데이터에서 검색 + 근거 인용 답변
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

    c1, c2 = st.columns([3, 1])
    with c2:
        year = st.selectbox("연도 필터", ["전체", "2023", "2024", "2025"], index=0)
    with c1:
        question = st.text_input("질문", placeholder="예: 2024년 환경표지 정의 인지율은?")

    if not question:
        return

    try:
        from rag.answer import answer as rag_answer
    except Exception as error:
        st.error(f"RAG 모듈을 불러오지 못했습니다: {error}")
        return

    with st.spinner("검색하고 근거로 답하는 중..."):
        try:
            result = rag_answer(question, k=5, year=None if year == "전체" else year)
        except Exception as error:
            st.error(
                f"검색/답변 중 오류: {error}\n\n"
                "인덱스가 없다면 먼저 `uv run python rag/index.py` 를 실행하세요."
            )
            return

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


# -----------------------------------------------------------------------------
# 5) 가이드 스텝퍼 — 업로드 → 인제스트 → 검수 → 인덱싱 → 질의(Q&A)
#    탭 대신 '순서가 있는 단계'로 안내한다. 각 단계는 앞 단계가 끝나야 열린다.
# -----------------------------------------------------------------------------
DATA_DIR = Path("data")

NO_KEY_MSG = (
    "OPENAI_API_KEY 를 찾을 수 없습니다.\n\n"
    "프로젝트 폴더에 `.env` 파일을 만들고 `OPENAI_API_KEY=sk-...` 한 줄을 넣어주세요."
)

# (번호, 라벨, 키) — 화면 순서
STEPS = [
    (1, "📤 업로드", "upload"),
    (2, "⚙️ 인제스트", "ingest"),
    (3, "🔍 검수", "review"),
    (4, "📚 인덱싱", "index"),
    (5, "💬 질의(Q&A)", "qa"),
    (6, "🚦 신호등", "signal"),
]
# 임베딩/추출/답변에 API Key 가 필요한 단계
STEPS_NEED_KEY = {2, 4, 5}

# D2: 각 단계에서 '지금 무엇을 해야 하는지' 한 줄 안내(행동 유도)
STEP_TODO = {
    1: "분석할 보고서 PDF를 올리고 'data/ 에 저장'을 누르세요.",
    2: "'전체 실행'을 눌러 추출~검수큐까지 처리하세요. (완료까지 수 분 걸릴 수 있어요)",
    3: "🔴 값 없는 행부터 골라 원문을 보고 값을 확정(저장)하세요.",
    4: "준비 게이트를 통과하면 '인덱싱 실행'을 누르세요.",
    5: "데이터에 대해 질문을 입력하세요. (출처 인용 답변)",
    6: "연도별 추세 신호등을 살펴보세요. 색은 추세 방향(상승/보합/하락)입니다.",
}


def render_next_step_nav(ctx: dict, step: int) -> None:
    """ D2: 현재 단계를 마치면 다음 단계로 가는 버튼/안내. """
    if step >= len(STEPS):
        return
    nxt = step + 1
    label = next(l for n, l, _ in STEPS if n == nxt)
    st.divider()
    if can_enter(nxt, ctx):
        # 라벨에 'N.' 번호는 넣지 않는다(상단 단계 네비 버튼과 텍스트 충돌 방지).
        if st.button(f"다음 단계로 → {label}", type="primary", key=f"goto_next_{step}"):
            st.session_state.step = nxt
            st.rerun()
    else:
        st.caption(f"🔒 다음 단계({label})는 이 단계를 끝내야 열립니다.")


def _data_pdfs() -> list[Path]:
    return sorted(DATA_DIR.glob("*.pdf")) if DATA_DIR.exists() else []


def _index_count() -> int:
    """ 현재 Chroma 인덱스의 청크 수(없으면 0). """
    try:
        from rag.index import get_collection
        return get_collection().count()
    except Exception:
        return 0


def _review_remaining_high() -> int:
    """ 검수 큐에서 아직 사람이 확정하지 않은 high 우선순위 행 수. """
    q = load_review_queue() or []
    reviewed = corrections.reviewed_keys()
    return sum(1 for r in q
               if (r.get("review_priority") == "high"
                   and corrections.row_key(r) not in reviewed))


def build_ctx() -> dict:
    """ 단계 게이트/상태 패널이 쓰는 현재 데이터 상태. """
    return {
        "pdf_count": len(_data_pdfs()),
        "review_queue": REVIEW_QUEUE_PATH.exists(),
        "remaining_high": _review_remaining_high() if REVIEW_QUEUE_PATH.exists() else 0,
        "index_count": _index_count(),
    }


def can_enter(step_no: int, ctx: dict) -> bool:
    """ 이 단계에 들어갈 수 있는가(앞 단계 산출물이 준비됐는가). """
    if step_no == 1:
        return True
    if step_no == 2:
        return ctx["pdf_count"] > 0          # 업로드된 PDF 가 있어야 인제스트
    if step_no in (3, 4):
        return ctx["review_queue"]           # 인제스트 산출(검수 큐)이 있어야
    if step_no == 5:
        return ctx["index_count"] > 0        # 인덱스가 있어야 질의
    if step_no == 6:
        return ctx["review_queue"]           # 정형 데이터(검수 큐 산출)가 있으면 추세 표시
    return False


def _is_done(step_no: int, ctx: dict) -> bool:
    if step_no == 1:
        return ctx["pdf_count"] > 0
    if step_no == 2:
        return ctx["review_queue"]
    if step_no == 3:
        return ctx["review_queue"] and ctx["remaining_high"] == 0
    if step_no == 4:
        return ctx["index_count"] > 0
    return False


def _step_state(step_no: int, ctx: dict, current: int) -> str:
    if step_no == current:
        return "current"
    if _is_done(step_no, ctx):
        return "done"
    if can_enter(step_no, ctx):
        return "open"
    return "locked"


# -----------------------------------------------------------------------------
# 스텝퍼 헤더(네비) + 상태 패널 + 시스템 로그 패널
# -----------------------------------------------------------------------------
def render_stepper_nav(ctx: dict, current: int) -> None:
    cols = st.columns(len(STEPS))
    icon = {"current": "▶", "done": "✅", "open": "○", "locked": "🔒"}
    for col, (no, label, _key) in zip(cols, STEPS):
        with col:
            state = _step_state(no, ctx, current)
            # Playwright/검증용 상태 센티넬
            st.markdown(
                f"<span data-testid='step{no}-status' style='display:none'>{state}</span>",
                unsafe_allow_html=True,
            )
            if st.button(f"{icon[state]} {no}. {label}", key=f"nav_{no}",
                         disabled=(state == "locked"), use_container_width=True):
                st.session_state.step = no
                st.rerun()


def render_status_panel(ctx: dict, gate=None) -> None:
    with st.sidebar:
        st.header("📊 데이터 상태")
        st.metric("업로드된 PDF", ctx["pdf_count"])
        st.metric("인덱싱된 청크", ctx["index_count"])
        st.metric("검수 남은 high", ctx["remaining_high"])

        # D5: 인덱스 정합(현재 데이터가 게이트를 통과하는가)
        if ctx["index_count"] > 0 and gate is not None:
            if gate.ok:
                st.success("인덱스 정합: ✅ 게이트 통과 데이터")
            else:
                st.warning("인덱스 정합: ⚠️ 미통과(미확정/빈값/미검수 포함 가능) — 검수 후 재인덱싱 권장")

        st.divider()
        # 다음 할 일 안내
        if ctx["pdf_count"] == 0:
            nxt = "1단계에서 보고서 PDF를 업로드하세요."
        elif not ctx["review_queue"]:
            nxt = "2단계에서 인제스트를 실행하세요."
        elif ctx["remaining_high"] > 0:
            nxt = f"3단계에서 high {ctx['remaining_high']}건을 검수하세요."
        elif ctx["index_count"] == 0:
            nxt = "4단계에서 인덱싱하세요."
        else:
            nxt = "5단계에서 질문하세요. (준비 완료)"
        st.info(f"👉 다음 할 일: {nxt}")


def render_log_panel() -> None:
    st.divider()
    with st.expander("🩺 시스템 로그", expanded=False):
        # (1) 진행 중인 인제스트 단계의 실시간 로그(run 로그)
        ing = st.session_state.get("ingest")
        if ing:
            key = ing["order"][min(ing["idx"], len(ing["order"]) - 1)]
            runlog = pipeline.step_log_path(ing["run_id"], key)
            st.caption(f"인제스트 run 로그: {runlog.name}")
            st.code(pipeline.tail(runlog, 30) or "(아직 없음)", language="log")

        # (2) 앱 로그
        lf = st.session_state.get("logfile")
        st.caption(f"앱 로그: {lf}")
        if lf and Path(lf).exists():
            tail = "\n".join(
                Path(lf).read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
            )
            st.code(tail or "(로그 비어 있음)", language="log")
        else:
            st.caption("앱 로그 파일이 아직 없습니다.")


# -----------------------------------------------------------------------------
# 단계별 화면
# -----------------------------------------------------------------------------
def render_step_upload(ctx: dict) -> None:
    st.subheader("📤 1단계 · 보고서 업로드")
    st.caption("분석할 인지도 조사 PDF를 올리면 `data/` 에 저장되고, 다음 단계(인제스트)의 입력이 됩니다.")

    up = st.file_uploader("PDF 업로드", type=["pdf"], accept_multiple_files=False)
    if up is not None:
        dest = DATA_DIR / up.name
        st.write(f"선택한 파일: **{up.name}** ({len(up.getvalue()):,} bytes)")
        if st.button(f"💾 data/ 에 저장", key="save_pdf"):
            DATA_DIR.mkdir(exist_ok=True)
            dest.write_bytes(up.getvalue())
            # 페이지 수 표시(검증/안내용)
            try:
                pages = len(pypdf.PdfReader(io.BytesIO(up.getvalue())).pages)
            except Exception:
                pages = "?"
            log.info("업로드 저장: %s (%s pages)", up.name, pages)
            st.success(f"✅ 저장했습니다: data/{up.name} (페이지 {pages}). 2단계(인제스트)로 진행하세요.")
            st.rerun()

    st.markdown("**현재 `data/` 의 PDF:**")
    pdfs = _data_pdfs()
    if pdfs:
        for p in pdfs:
            st.write(f"- {p.name} ({p.stat().st_size:,} bytes)")
    else:
        st.caption("아직 없습니다.")


def _ingest_init(pdf_name: str, force: bool = False) -> None:
    """ 인제스트 체인 상태를 초기화하고 첫 단계를 띄운다. force=True 면 스킵 없이 전부 재실행. """
    st.session_state.ingest = {
        "run_id": pipeline.new_run_id(),
        "pdf": pdf_name,
        "order": [s.key for s in pipeline.INGEST_STEPS],
        "idx": 0, "status": "running",
        "started": {}, "ended": {}, "rc": {}, "skipped": [], "pid": {},
        "force": force,
    }
    pipeline.save_state(st.session_state.ingest)
    _ingest_launch_current()


def _ingest_advance() -> None:
    """ 다음 단계로 넘긴다(없으면 완료). """
    ing = st.session_state.ingest
    ing["idx"] += 1
    if ing["idx"] >= len(ing["order"]):
        ing["status"] = "done"
        st.session_state.ingest_proc = None
        pipeline.save_state(ing)
    else:
        _ingest_launch_current()


def _ingest_recover() -> None:
    """ 새로고침으로 세션이 날아간 뒤, 디스크의 인제스트 상태가 'running'이면 복구한다.
        Popen 핸들은 못 살리므로, 복구 세션은 pid 생존/산출파일로 진행을 이어간다. """
    if st.session_state.get("ingest"):
        return                         # 세션에 이미 있으면(정상 진행 중) 복구 불필요
    saved = pipeline.load_state()
    if not saved or saved.get("status") != "running":
        return
    st.session_state.ingest = saved
    st.session_state.ingest_proc = None    # 복구엔 Popen 없음 → pid 경로로 모니터
    st.session_state.ingest_recovered = True
    st.session_state.step = 2              # 진행 중 인제스트를 볼 수 있게 2단계로
    log.info("인제스트 복구: run=%s idx=%s", saved.get("run_id"), saved.get("idx"))


def _ingest_launch_current() -> None:
    """ 현재 단계를 실행한다. D1: 입력이 최신이면 LLM 호출 없이 '스킵'하고 다음으로. """
    ing = st.session_state.ingest
    key = ing["order"][ing["idx"]]
    step = pipeline.STEP_BY_KEY[key]
    ing["started"][key] = time.time()

    # 스킵 캐시: 강제 재실행이 아니고 산출이 최신이면 건너뛴다(연쇄적으로).
    if not ing.get("force") and pipeline.is_fresh(step, ing["pdf"]):
        ing["ended"][key] = ing["started"][key]
        ing["rc"][key] = 0
        ing["skipped"].append(key)
        st.session_state.ingest_proc = None
        log.info("인제스트 단계 스킵(최신): %s", key)
        pipeline.save_state(ing)
        _ingest_advance()
        return

    proc = pipeline.launch(ing["run_id"], key, pdf_name=ing["pdf"] if key == "extract" else None)
    st.session_state.ingest_proc = proc
    ing.setdefault("pid", {})[key] = proc.pid    # 복구 시 pid 로 생존 확인
    pipeline.save_state(ing)
    log.info("인제스트 단계 시작: %s (run=%s pid=%s)", key, ing["run_id"], proc.pid)


@st.fragment(run_every=2)
def _ingest_monitor() -> None:
    """ 2초마다 현재 단계의 진행/로그를 갱신하고, 끝나면 다음 단계로 넘긴다. """
    ing = st.session_state.get("ingest")
    if not ing:
        return
    order, idx = ing["order"], ing["idx"]
    proc = st.session_state.get("ingest_proc")

    # 진행 중인 단계가 끝났으면 상태 전이(다음 단계 launch 또는 완료/에러).
    # 정상 세션은 Popen(returncode)으로, 복구 세션(Popen 없음)은 pid 생존+산출파일로 판정.
    if ing["status"] == "running":
        key = order[idx]
        if proc is not None:
            finished = not pipeline.alive(proc)
            rc = proc.returncode if finished else None
        else:
            # 복구 세션(Popen 없음): pid 생존+산출파일로 판정.
            res = pipeline.recover_step_result(
                pipeline.STEP_BY_KEY[key], ing["pdf"],
                ing.get("pid", {}).get(key), ing["started"].get(key, 0))
            finished = res is not None
            rc = None if res is None else (0 if res == "ok" else 1)
        if finished:
            ing["ended"][key] = time.time()
            ing["rc"][key] = rc
            log.info("인제스트 단계 종료: %s rc=%s", key, rc)
            if rc != 0:
                ing["status"] = "error"
                pipeline.save_state(ing)
            else:
                _ingest_advance()

    # 진행 표시
    if st.session_state.get("ingest_recovered") and ing["status"] == "running":
        st.info("↻ 새로고침 전 진행 중이던 인제스트를 이어받았습니다.")
    done = ing["idx"]
    total = len(order)
    frac = 1.0 if ing["status"] == "done" else min((done + 0.5) / total, 0.99)
    st.progress(frac, text=f"인제스트: {ing['status']} — {done}/{total} 단계 완료")

    for i, k in enumerate(order):
        s = pipeline.STEP_BY_KEY[k]
        if k in ing["ended"]:
            if k in ing.get("skipped", []):
                st.write(f"⏭️ {s.title} — 스킵(최신)")
                continue
            dt = ing["ended"][k] - ing["started"][k]
            mark = "✅" if ing["rc"].get(k) == 0 else "❌"
            st.write(f"{mark} {s.title} — {dt:.1f}s")
        elif i == done and ing["status"] == "running":
            dt = time.time() - ing["started"].get(k, time.time())
            st.write(f"▶ {s.title} … 진행 중 ({dt:.0f}s)")
            tail = pipeline.tail(pipeline.step_log_path(ing["run_id"], k), 20)
            st.code(tail or "(시작 중…)", language="log")
        else:
            st.write(f"⏳ {s.title}")

    if ing["status"] == "done":
        st.success("✅ 인제스트 완료! 3단계(검수)에서 확인 후 4단계(인덱싱)로 진행하세요.")
    elif ing["status"] == "error":
        st.error(f"⛔ '{order[done]}' 단계 실패 (아래 🩺 시스템 로그/해당 단계 로그 확인). 원인 해결 후 다시 실행하세요.")
    elif ing["status"] == "cancelled":
        st.warning("취소되었습니다.")


def render_step_ingest(ctx: dict) -> None:
    st.subheader("⚙️ 2단계 · 인제스트 (추출 → 표준화 → 정제 → 검수 큐)")
    st.caption(
        "선택한 PDF에서 수치를 추출하고 표준화·정제해 검수 큐까지 만듭니다. "
        "⚠️ 표준화 이후는 `outputs/*.extracted.jsonl` 전체를 다시 처리하므로 여러 연도가 있으면 시간이 걸립니다."
    )

    pdfs = _data_pdfs()
    if not pdfs:
        st.warning("먼저 1단계에서 PDF를 업로드하세요.")
        return

    # 최신 연도 PDF를 기본값으로(파일명이 연도로 시작 → 내림차순). 가장 흔히 처리할 보고서.
    sel = st.selectbox("추출할 PDF", [p.name for p in sorted(pdfs, key=lambda p: p.name, reverse=True)])
    force = st.checkbox("강제 재실행(스킵 안 함)", value=False,
                        help="끄면 산출이 최신인 단계는 건너뜁니다(빠름). 켜면 전부 다시 실행합니다.")
    ing = st.session_state.get("ingest")
    running = bool(ing and ing["status"] == "running")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶ 전체 실행", disabled=running, key="ingest_run"):
            _ingest_init(sel, force=force)
            st.rerun()
    with c2:
        if st.button("■ 취소", disabled=not running, key="ingest_cancel"):
            proc = st.session_state.get("ingest_proc")
            if proc is not None:
                pipeline.cancel(proc)
            elif ing:    # 복구 세션: Popen 없음 → 현재 단계 pid 로 종료
                key = ing["order"][ing["idx"]]
                pipeline.cancel_pid(ing.get("pid", {}).get(key))
            if ing:
                ing["status"] = "cancelled"
                pipeline.save_state(ing)
            log.info("인제스트 취소")
            st.rerun()

    if ing:
        _ingest_monitor()


def render_step_index(ctx: dict, gate=None) -> None:
    st.subheader("📚 4단계 · 인덱싱 (준비 게이트)")
    st.caption("아래 준비 게이트를 통과해야 인덱싱할 수 있습니다. (추측은 데이터가 아니다 — 확정 사실만 인덱싱)")
    rep = gate
    if rep is None:
        try:
            from rag.validate import validate_ready
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
                from rag import chunking
                from rag import index as indexmod
                st.write("청킹(확정 사실 → 청크)…")
                chunks = chunking.build_chunks(chunking.load_rows())
                chunking.save_chunks(chunks)
                st.write(f"청크 {len(chunks)}개 — 임베딩·Chroma 인덱싱…")
                n = indexmod.build_index(reset=True)
                log.info("인덱싱 완료: %d 청크", n)
                status.update(label=f"✅ 인덱싱 완료: {n} 청크", state="complete")
            except Exception as error:
                log.exception("인덱싱 실패")
                status.update(label=f"⛔ 인덱싱 실패: {error}", state="error")
        st.rerun()

    st.write(f"현재 인덱스 청크: {ctx['index_count']}")


# 핵심 정책 지표 탭: 사용자가 추적하고 싶어 한 지표를 std_id 로 묶는다.
# 데이터에 실제 있는 std_id 만 그린다(없으면 '데이터 없음'으로 정직하게 표시 — 추측 금지).
PRIORITY_GROUPS = [
    ("주요 인증제도 인지도 추이", ["녹색제품_인지도", "환경표지_인지도",
                                    "환경성적표지_인지도", "저탄소제품_인지도"]),
    ("친환경 제품 구매·관심도", ["친환경제품_구매경험", "친환경제품_관심도", "환경문제_관심도"]),
    ("그린카드 성과 지표", ["그린카드_발급사용의향", "그린카드_사용여부",
                            "그린카드_전반만족도", "그린카드_포인트기부의향"]),
    ("경제적 가치(추가 지불의사)", ["친환경제품_추가지불의향"]),
]


def _trend_altair(series_list):
    """ signals.Series 목록 → 연도별 추세 멀티라인 Altair 차트(없으면 None).
        x=연도를 '실제 간격'으로 둬서 끊긴 구간(예: 2017→2023)은 길게 보이게 한다
        (균등 간격으로 두면 6년 공백이 한 스텝처럼 보여 '가짜 인접 점프'가 됨).
        점은 값이 실제 있는 연도에만 찍는다 — 보간/추측 없음. """
    import altair as alt
    import pandas as pd

    recs = [{"연도": p.year, "값": p.value, "응답": s.label, "단위": s.unit or ""}
            for s in series_list for p in s.points]
    if not recs:
        return None
    df = pd.DataFrame(recs)
    chart = (
        alt.Chart(df)
        .mark_line(point=True)
        .encode(
            x=alt.X("연도:Q", axis=alt.Axis(format="d", tickMinStep=1, title="연도")),
            y=alt.Y("값:Q", title="%"),
            color=alt.Color("응답:N", title="응답 항목",
                            legend=alt.Legend(orient="bottom", columns=2)),
            tooltip=[alt.Tooltip("응답:N", title="응답"),
                     alt.Tooltip("연도:Q", format="d"),
                     alt.Tooltip("값:Q", title="값"),
                     alt.Tooltip("단위:N", title="단위")],
        )
        .properties(height=300)
    )
    return chart


def _render_indicator_card(ind, threshold, max_series: int = 6):
    """ 한 지표(Indicator)를 카드로: 멀티라인 차트 + 응답별 최신값·신호 + 출처.
        라벨이 많으면 변화 큰 max_series 개만 차트에 그린다(compute_signals 가 정렬해 둠). """
    top_series = ind.series[:max_series]
    with st.container(border=True):
        st.markdown(f"**{ind.label}**")
        if ind.summary:
            st.caption(ind.summary)
        chart = _trend_altair(top_series)
        if chart is not None:
            st.altair_chart(chart, use_container_width=True)
        for s in top_series:
            sig = s.signal(threshold)
            em = signals.SIGNAL_EMOJI.get(sig, "·")
            if sig:
                tail = f" ({s.delta:+}%p {signals.SIGNAL_TEXT[sig]})"
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

    pairs = sorted([(l, v) for l, v in pairs if v is not None], key=lambda t: -t[1])
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


def _raw_indicators_by_category(rows, category):
    """ 원본 행에서 해당 카테고리의 (std_id, std_label, 연도들). 단일연도 항목도 포함
        (파레토는 한 해 스냅샷이라 시계열 최소커버리지에 안 걸려도 보여줄 수 있다). """
    out: dict[str, dict] = {}
    for r in rows:
        if (r.get("category") or "").strip() != category:
            continue
        if not (r.get("value") or "").strip():
            continue
        sid = (r.get("std_id") or "").strip()
        if not sid:
            continue
        d = out.setdefault(sid, {"label": (r.get("std_label") or sid).strip(), "years": set()})
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


def _render_judgment_tab(all_inds):
    """ '판단 기준' 탭: 친환경제품 판단 기준의 연도별 구성(누적막대). """
    st.caption("연도별로 각 판단 기준의 응답률(%)을 색으로 쌓아 구성 변화를 봅니다. "
               "다중응답이라 한 해 막대의 합은 100%를 넘을 수 있습니다.")
    cands = _inds_by_substr(all_inds, "판단기준")
    if not cands:
        st.info("판단 기준 시계열 데이터가 없습니다.")
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
        st.altair_chart(chart, use_container_width=True)
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
        st.altair_chart(chart, use_container_width=True)
    st.caption(f"📅 {years[0]}~{years[-1]} · 일관 경로 {len(kept)}개"
               + (f" (표기가 흔들리는 {dropped}개는 제외)" if dropped else ""))
    head = kept[0]
    st.caption(f"[출처: {head.source} p.{head.page}]")


def _render_barrier_tab(rows, terms):
    """ '구매 장벽' 탭: 선택 연도의 장애 요인을 파레토 차트로(빈도순 + 누적%). """
    st.caption("구매를 주저하는 이유를 빈도 내림차순 막대 + 누적 % 꺾은선(파레토)으로 봅니다. "
               "어떤 이유부터 해결하면 효과가 큰지 우선순위를 보여줍니다.")
    cands = _raw_indicators_by_category(rows, "구매 장벽")
    if not cands:
        st.info("구매 장벽 데이터가 없습니다.")
        return
    if terms:
        # 입력어로 좁히기: 지표명 + 그 지표의 응답 라벨(이유)까지 포함해 매칭한다.
        hay: dict[str, str] = {}
        for r in rows:
            if (r.get("category") or "").strip() != "구매 장벽":
                continue
            sid = (r.get("std_id") or "").strip()
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
    st.altair_chart(chart, use_container_width=True)
    src = next((r.get("source", "") for r in rows
                if (r.get("std_id") or "").strip() == sid and (r.get("year") or "").strip() == year), "")
    st.caption(f"📊 {lab} · {year}년 · 응답 {len(pairs)}개 · [출처: {src}]")


def render_step_signal(ctx: dict) -> None:
    """ 6단계 · 실시간 신호등. 정형 데이터의 응답 항목을 연도별로 이어 추세 신호로 표시. """

    st.subheader("🚦 6단계 · 실시간 신호등 (연도별 추세)")
    st.caption(
        "정형 데이터의 응답 항목을 연도별로 이어 추세를 신호로 보여줍니다. "
        "색은 **추세 방향**입니다 — 🟢 상승 · 🟡 보합 · 🔴 하락 "
        "(좋음/나쁨 같은 가치판단이 아닙니다). 데이터에 실제 있는 값·출처만 씁니다."
    )

    try:
        rows = chunking.load_rows()
    except Exception as error:
        st.warning(f"먼저 인제스트(2단계)로 정형 데이터를 만들어 주세요. ({error})")
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

    all_inds = signals.compute_signals(rows, threshold_pp=threshold, min_coverage=2)
    all_inds = _filter_inds(all_inds, terms)   # 입력한 질문에 해당하는 지표만 남긴다
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

    tab_trend, tab_core, tab_judge, tab_chan, tab_barrier = st.tabs(
        ["🚦 추세 신호", "📊 핵심 정책 지표", "🧭 판단 기준", "📡 인지 경로", "🚧 구매 장벽"])

    with tab_trend:
        counts = signals.summarize(inds, threshold)
        c1, c2, c3 = st.columns(3)
        c1.metric("🟢 상승", counts["up"])
        c2.metric("🟡 보합", counts["flat"])
        c3.metric("🔴 하락", counts["down"])

        # 가장 큰 변화(최근 두 연도) — 신호 있는 시계열만, |Δ| 큰 순 8개 카드.
        st.markdown("#### 가장 큰 변화 (최근 두 연도)")
        movers = [(ind, s) for ind in inds for s in ind.series if s.signal(threshold)]
        movers.sort(key=lambda t: abs(t[1].delta), reverse=True)
        cols = st.columns(4)
        for i, (ind, s) in enumerate(movers[:8]):
            sig = s.signal(threshold)
            with cols[i % 4]:
                st.metric(
                    label=f"{signals.SIGNAL_EMOJI[sig]} {ind.label} · {s.label}",
                    value=f"{s.latest.value}{s.unit}",
                    delta=f"{s.delta:+}%p",
                    delta_color="normal" if sig in ("up", "down") else "off",
                )
                st.caption(f"[출처: {s.source} p.{s.page}]")

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
        _render_judgment_tab(all_inds)

    with tab_chan:
        _render_channel_tab(all_inds)

    with tab_barrier:
        _render_barrier_tab(rows, terms)


# -----------------------------------------------------------------------------
# 6) 진입점: 가이드 스텝퍼
# -----------------------------------------------------------------------------
def main():
    # 로깅 먼저(멱등). 로그 파일 경로는 세션에 보관(로그 패널이 tail).
    logfile = setup_logging("app")
    st.session_state.setdefault("logfile", str(logfile))
    st.session_state.setdefault("step", 1)
    _ingest_recover()    # 새로고침으로 세션이 날아갔어도 진행 중 인제스트를 이어받는다.

    st.set_page_config(page_title="k-green-signal", layout="wide")
    st.title("🚦 대한민국 친환경 소비 인지도 실시간 신호등")
    st.caption("k-green-signal · 업로드 → 인제스트 → 검수 → 인덱싱 → 질의")

    api_key = get_api_key()
    ctx = build_ctx()
    log.info("앱 렌더 — step=%s, pdf=%s, idx=%s, api_key=%s",
             st.session_state.step, ctx["pdf_count"], ctx["index_count"],
             "있음" if api_key else "없음")

    # 준비 게이트는 인덱스 정합 경고(D5)·인덱싱(4)에서 공유하므로 한 번만 계산.
    gate = None
    if ctx["review_queue"]:
        try:
            from rag.validate import validate_ready
            gate = validate_ready(strict=True)
        except Exception:
            gate = None

    render_status_panel(ctx, gate)
    render_stepper_nav(ctx, st.session_state.step)
    st.divider()

    step = st.session_state.step
    # D2: 이 단계에서 '지금 할 일' 한 줄 안내(설명은 각 단계 화면 상단 caption 에).
    if step in STEP_TODO:
        st.info(f"👣 지금 할 일 — {STEP_TODO[step]}")

    if step in STEPS_NEED_KEY and api_key is None:
        st.error(NO_KEY_MSG)
    elif step == 1:
        render_step_upload(ctx)
    elif step == 2:
        render_step_ingest(ctx)
    elif step == 3:
        render_review_tab()
    elif step == 4:
        render_step_index(ctx, gate)
    elif step == 5:
        render_rag_tab(gate)
    elif step == 6:
        render_step_signal(ctx)

    # D2: 다음 단계로 가는 행동 안내(앞 단계 산출물이 준비됐으면 버튼, 아니면 잠금 안내)
    render_next_step_nav(ctx, step)
    render_log_panel()


if __name__ == "__main__":
    main()
