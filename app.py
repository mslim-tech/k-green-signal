# app.py
# -----------------------------------------------------------------------------
# RAG Lab - Phase 1: Baseline 문서 업로드 Q&A 앱 (Long Context 방식)
#
# 이 파일의 역할:
#   - 사용자가 사이드바에서 문서(PDF/TXT/DOCX)를 업로드하면 텍스트를 추출한다.
#   - 추출한 문서 전체 텍스트와 사용자 질문을 함께 프롬프트에 넣어
#     OpenAI 모델에게 답변을 받는다.
#   - 아직 Chunking / Embedding / Vector DB / Retriever 는 사용하지 않는다.
#     (문서 전체를 그대로 프롬프트에 넣는 가장 단순한 Baseline 방식)
# -----------------------------------------------------------------------------

import io
import os

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

import pypdf
import docx

# 사용할 모델은 중앙 설정(rag/config.py)에서 가져온다. (현재 gpt-5.4-mini)
from rag.config import ANSWER_MODEL as MODEL_NAME


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
# 4) Streamlit 화면 구성
# -----------------------------------------------------------------------------
def main():
    st.title("📄 문서 Q&A (Baseline)")
    st.caption("문서를 업로드하고 질문하면, 문서 내용을 바탕으로 답해드립니다.")

    # --- API Key 확인 ---
    api_key = get_api_key()
    if not api_key:
        st.error(
            "OPENAI_API_KEY 를 찾을 수 없습니다.\n\n"
            "프로젝트 폴더에 `.env` 파일을 만들고 아래 한 줄을 넣어주세요:\n\n"
            "`OPENAI_API_KEY=sk-...`"
        )
        st.stop()

    client = OpenAI(api_key=api_key)

    # --- 대화 기록 저장소 준비 (세션 상태) ---
    if "messages" not in st.session_state:
        st.session_state.messages = []   # [{"role": "user"/"assistant", "content": "..."}]
    if "document_text" not in st.session_state:
        st.session_state.document_text = None

    # --- 사이드바: 파일 업로드 ---
    with st.sidebar:
        st.header("📁 문서 업로드")
        uploaded_file = st.file_uploader(
            "PDF, TXT, DOCX 파일을 올려주세요.",
            type=["pdf", "txt", "docx"],
        )

        if uploaded_file is not None:
            text, error_message = extract_text(uploaded_file)
            if error_message:
                # 추출 실패 시 안내하고 문서 상태는 비워둔다.
                st.error(error_message)
                st.session_state.document_text = None
            else:
                st.session_state.document_text = text
                # 요구사항: 파일명 / 형식 / 추출된 텍스트 길이 표시
                file_extension = uploaded_file.name.rsplit(".", 1)[-1].upper()
                st.success("문서를 읽었습니다.")
                st.write(f"**파일명:** {uploaded_file.name}")
                st.write(f"**형식:** {file_extension}")
                st.write(f"**추출된 텍스트 길이:** {len(text):,} 자")

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


if __name__ == "__main__":
    main()
