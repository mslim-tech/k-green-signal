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
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

import pypdf
import docx

# 사용할 모델은 중앙 설정(rag/config.py)에서 가져온다. (현재 gpt-5.4-mini)
from rag.config import ANSWER_MODEL as MODEL_NAME

# 검수 기록 저장/적용 로직 (5단계)
from rag import corrections

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
# 2) 텍스트 추출 함수
#    - 파일 형식(PDF/TXT/DOCX)에 따라 다른 방법으로 텍스트를 뽑는다.
#    - 오류가 나면 사용자가 이해할 수 있는 메시지를 함께 돌려준다.
#      반환값: (추출된_텍스트, 오류_메시지)  -> 정상이면 오류_메시지 는 None
# -----------------------------------------------------------------------------
def extract_pdf(file_bytes):
    """ PDF 파일에서 페이지별 텍스트를 추출한다. (pypdf 사용) """
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        # 페이지에 텍스트가 없을 수도 있으므로 빈 문자열로 안전하게 처리한다.
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def extract_txt(file_bytes):
    """ TXT 파일 내용을 그대로 읽는다. UTF-8 우선, 실패 시 cp949 로 재시도한다. """
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # 한글 윈도우에서 만든 텍스트 파일은 cp949(euc-kr) 인 경우가 많다.
        return file_bytes.decode("cp949")


def extract_docx(file_bytes):
    """ DOCX 파일에서 문단 텍스트를 추출한다. (python-docx 사용) """
    document = docx.Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in document.paragraphs]
    return "\n".join(paragraphs)


def extract_text(uploaded_file):
    """
    업로드된 파일에서 텍스트를 추출한다.
    반환값: (텍스트, 오류메시지)
      - 성공: (텍스트, None)
      - 실패: (None, "사용자에게 보여줄 안내 문구")
    """
    file_bytes = uploaded_file.getvalue()
    # 파일 확장자를 소문자로 통일해서 형식을 판단한다.
    file_name = uploaded_file.name
    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

    try:
        if extension == "pdf":
            text = extract_pdf(file_bytes)
        elif extension == "txt":
            text = extract_txt(file_bytes)
        elif extension == "docx":
            text = extract_docx(file_bytes)
        else:
            return None, f"지원하지 않는 형식입니다: .{extension} (PDF, TXT, DOCX 만 가능합니다)"
    except Exception as error:
        # 어떤 오류든 사용자가 이해할 수 있는 안내로 바꿔서 돌려준다.
        return None, f"'{file_name}' 에서 텍스트를 추출하지 못했습니다. 파일이 손상되었거나 형식이 올바른지 확인해주세요. (상세: {error})"

    if not text.strip():
        return None, f"'{file_name}' 에서 읽을 수 있는 텍스트를 찾지 못했습니다. (스캔 이미지 PDF 일 수 있습니다)"

    return text, None


# -----------------------------------------------------------------------------
# 3) OpenAI 에게 답변 요청
#    - 문서 내용과 사용자 질문을 함께 프롬프트에 넣는다. (Long Context 방식)
#    - Phase 1 baseline 이므로 각 질문을 독립적으로 처리한다. (이전 대화 미포함)
# -----------------------------------------------------------------------------
def ask_openai(client, document_text, question):
    """ 문서 내용 + 질문을 프롬프트로 만들어 OpenAI 답변을 받는다. """
    system_prompt = (
        "너는 업로드된 문서를 근거로 답하는 도우미야. "
        "반드시 문서 내용에 기반해서 답하고, 문서에 없는 내용은 추측하지 말고 "
        "'문서에서 찾을 수 없습니다'라고 답해줘. 한국어로 친절하게 답해줘."
    )
    user_prompt = (
        "[문서 내용]\n"
        f"{document_text}\n\n"
        "[질문]\n"
        f"{question}"
    )

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content


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
    done_n = sum(1 for r in queue if corrections.row_key(r) in reviewed)
    st.caption(f"표시 {len(filtered)}행 / 전체 {len(queue)}행 · 검수 완료 {done_n}행")

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
def render_rag_tab():
    st.subheader("🔎 데이터 질의 (RAG · 근거 인용)")
    st.caption(
        "정형화된 인지도 조사 데이터에서 검색해 **출처(파일·페이지)를 인용**해 답합니다. "
        "근거가 없으면 '문서에서 찾을 수 없습니다'라고 답합니다."
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
    with st.expander(f"📎 근거 출처 {len(result.hits)}건 보기"):
        for i, h in enumerate(result.hits, start=1):
            st.markdown(
                f"**[{i}]** {h.metadata.get('year')}년 · `{h.metadata.get('std_id')}` "
                f"— {h.locator} (유사도 {h.score})"
            )
            st.caption(h.text.replace("\n", " ")[:200] + " ...")


# -----------------------------------------------------------------------------
# 5) 문서 Q&A 탭 (기존 Baseline)
# -----------------------------------------------------------------------------
def render_qa_tab(client):
    st.subheader("📄 문서 Q&A (Baseline)")
    st.caption("문서를 업로드하고 질문하면, 문서 내용을 바탕으로 답해드립니다.")

    # --- 대화 기록 저장소 준비 (세션 상태) ---
    if "messages" not in st.session_state:
        st.session_state.messages = []   # [{"role": "user"/"assistant", "content": "..."}]
    if "document_text" not in st.session_state:
        st.session_state.document_text = None

    # --- 사이드바: 파일 업로드 (여러 개 가능) ---
    with st.sidebar:
        st.header("📁 문서 업로드")
        uploaded_files = st.file_uploader(
            "PDF, TXT, DOCX 파일을 올려주세요. (여러 개 선택 가능)",
            type=["pdf", "txt", "docx"],
            accept_multiple_files=True,
        )

        if uploaded_files:
            # 여러 파일의 텍스트를 각각 추출해, 파일 경계를 표시하며 하나로 합친다.
            combined_parts = []
            ok_count = 0
            for uf in uploaded_files:
                text, error_message = extract_text(uf)
                ext = uf.name.rsplit(".", 1)[-1].upper() if "." in uf.name else "?"
                if error_message:
                    st.error(f"❌ {uf.name}: {error_message}")
                    continue
                # 모델이 어느 문서에서 온 내용인지 알 수 있도록 경계를 넣는다.
                combined_parts.append(f"[문서 시작: {uf.name}]\n{text}\n[문서 끝: {uf.name}]")
                ok_count += 1
                st.write(f"✅ **{uf.name}** ({ext}, {len(text):,}자)")

            if combined_parts:
                st.session_state.document_text = "\n\n".join(combined_parts)
                st.success(
                    f"문서 {ok_count}개를 읽었습니다. "
                    f"(합계 {len(st.session_state.document_text):,}자)"
                )
            else:
                st.session_state.document_text = None

    # --- 메인: 기존 대화 다시 보여주기 ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- 메인: 질문 입력 받기 ---
    question = st.chat_input("문서에 대해 질문해보세요.")
    if question:
        # 사용자 질문을 화면에 표시하고 기록에 저장
        with st.chat_message("user"):
            st.markdown(question)
        st.session_state.messages.append({"role": "user", "content": question})

        # 문서가 없으면 OpenAI 를 호출하지 않고 안내만 한다.
        if not st.session_state.document_text:
            answer = "먼저 문서를 업로드해주세요."
            with st.chat_message("assistant"):
                st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
        else:
            # 문서가 있으면 OpenAI 에게 답변을 요청한다.
            with st.chat_message("assistant"):
                with st.spinner("답변을 생성하고 있습니다..."):
                    try:
                        answer = ask_openai(
                            client,
                            st.session_state.document_text,
                            question,
                        )
                    except Exception as error:
                        # 문서가 너무 길어 한도를 넘거나, 네트워크/인증 문제일 수 있다.
                        answer = (
                            "답변을 생성하는 중 오류가 발생했습니다. "
                            "문서가 너무 길거나 네트워크/API Key 에 문제가 있을 수 있습니다. "
                            "잠시 후 다시 시도해주세요.\n\n"
                            f"(상세: {error})"
                        )
                st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})


# -----------------------------------------------------------------------------
# 6) 진입점: 탭 2개로 화면을 나눈다.
# -----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="RAG Lab", layout="wide")
    st.title("🧪 RAG Lab")

    # --- API Key 확인 (Q&A 탭에서만 필요하지만, 클라이언트는 한 번만 만든다) ---
    api_key = get_api_key()
    client = OpenAI(api_key=api_key) if api_key else None

    rag_tab, qa_tab, review_tab = st.tabs(["🔎 데이터 질의(RAG)", "📄 문서 Q&A", "🔍 검수"])

    no_key_msg = (
        "OPENAI_API_KEY 를 찾을 수 없습니다.\n\n"
        "프로젝트 폴더에 `.env` 파일을 만들고 아래 한 줄을 넣어주세요:\n\n"
        "`OPENAI_API_KEY=sk-...`"
    )

    with rag_tab:
        # RAG 검색·답변은 임베딩·답변에 API Key 가 필요하다.
        if client is None:
            st.error(no_key_msg)
        else:
            render_rag_tab()

    with qa_tab:
        if client is None:
            st.error(no_key_msg)
        else:
            render_qa_tab(client)

    with review_tab:
        # 검수는 API Key 없이도 동작한다(LLM 호출 없음).
        render_review_tab()


if __name__ == "__main__":
    main()
