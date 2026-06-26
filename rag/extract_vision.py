# rag/extract_vision.py
# -----------------------------------------------------------------------------
# 2단계(비전): 표가 깨진 블록을 'PDF 페이지 이미지'로 다시 읽는다.
#
# 왜 필요한가:
#   - 기존 extract.py 는 parsing.py 가 뽑은 '텍스트'(PyMuPDF get_text)를 LLM 에 준다.
#     그런데 결과보고서의 표는 2단(좌우) 배치라, 텍스트로 펼치면 열이 뒤섞이고
#     라벨과 숫자가 분리돼 LLM 이 값을 못 맞춘다(→ 빈칸/오정렬/행 누락).
#   - Claude 웹이 같은 표를 정확히 읽는 이유는 'PDF 를 이미지로(레이아웃 그대로)' 보기 때문.
#   - 그래서 이 모듈은 해당 페이지를 이미지로 렌더링해 '멀티모달' 모델에게 표를 읽힌다.
#
#   출력 형식은 extract.py 의 EXTRACTION_SCHEMA 를 그대로 재사용한다(같은 구조 보장).
#
# 보안: API Key 는 .env 의 OPENAI_API_KEY 에서만 읽는다(get_client 재사용).
#
# 단독 시험:
#   uv run python rag/extract_vision.py "2023년 인지도조사 결과보고서.pdf" 74 75
# -----------------------------------------------------------------------------

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import fitz  # PyMuPDF

try:
    from rag.extract import get_client, EXTRACTION_SCHEMA
    from rag.config import VISION_MODEL
except ImportError:
    from extract import get_client, EXTRACTION_SCHEMA
    from config import VISION_MODEL


DATA_DIR = Path("data")
RENDER_DPI = 200   # 숫자가 또렷하게 읽히는 해상도(너무 키우면 토큰/속도 부담)


VISION_SYSTEM = (
    "너는 설문 결과보고서 PDF '페이지 이미지'에서 표/수치를 정확히 읽어 "
    "'전체(국민 전체) 기준' 응답 분포를 구조화하는 도구다.\n"
    "규칙:\n"
    "- 표가 2단(좌·우 두 묶음)으로 나뉘어 있으면, 좌측 묶음을 위→아래로 모두 읽은 뒤 "
    "우측 묶음을 읽어 '라벨'과 '값'을 정확히 짝지어라. 한 행은 보통 (라벨, 사례수, %) 형태다.\n"
    "- response_items 에는 '응답 품목/보기 라벨'과 그 '% 값'만 넣어라. "
    "'전체/사례수/구분/구분(계속)/소계/합계/TOP' 같은 표 머리글·집계행은 품목이 아니므로 넣지 마라.\n"
    "- '전체' 기준 수치만. 성별/연령/지역 등 하위집단 수치는 넣지 마라.\n"
    "- 이미지에서 '명확히 보이는' 값만 넣고, 안 보이면 value 를 null 로. 값을 지어내지 마라.\n"
    "- base_n 은 표의 사례수(전체 N), unit 은 단위(보통 '%'). 복수응답 표시가 있으면 multi_response=true.\n"
    "- 모든 텍스트는 한국어."
)


def render_page_images(pdf_path: Path, page_start: int, page_end: int,
                       dpi: int = RENDER_DPI) -> list[bytes]:
    """ PDF 의 page_start~page_end(1-based, 포함) 페이지를 PNG 바이트 목록으로 렌더링. """
    doc = fitz.open(pdf_path)
    images: list[bytes] = []
    last = min(page_end, doc.page_count)
    for pageno in range(page_start, last + 1):
        pix = doc[pageno - 1].get_pixmap(dpi=dpi)   # fitz 는 0-based
        images.append(pix.tobytes("png"))
    doc.close()
    return images


def _resolve_pdf(source: str) -> Path:
    """ source(파일명)로 data/ 안의 실제 PDF 경로를 찾는다. """
    p = DATA_DIR / source
    if p.exists():
        return p
    # 혹시 경로가 통째로 들어온 경우
    p2 = Path(source)
    if p2.exists():
        return p2
    raise FileNotFoundError(f"PDF 를 찾을 수 없습니다: {source} (data/ 확인)")


def extract_pages_vision(client, source: str, page_start: int, page_end: int,
                         context: str = "", focus: str = "",
                         model: str = VISION_MODEL, retries: int = 2) -> dict:
    """
    지정한 PDF 페이지들을 이미지로 보내 표/수치를 구조화해 받는다.
    반환: EXTRACTION_SCHEMA 형식의 dict (question_summary, response_items, base_n, ...).
      context - 이 블록의 맥락(섹션/문항명/질문문) 텍스트. 어떤 표를 읽을지 안내.
      focus   - 특정 표(예: '<표 3-60>')만 집어 읽게 하는 추가 지시(선택).
    """
    pdf_path = _resolve_pdf(source)
    images = render_page_images(pdf_path, page_start, page_end)

    user_text = (
        "다음 페이지 이미지에서 '전체 기준' 응답 분포를 빠짐없이 읽어줘.\n"
        f"[맥락]\n{context or '(없음)'}\n"
    )
    if focus:
        user_text += f"\n[집중해서 읽을 표] {focus}\n"

    content = [{"type": "text", "text": user_text}]
    for png in images:
        b64 = base64.b64encode(png).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})

    last_error = None
    for _ in range(retries):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": VISION_SYSTEM},
                    {"role": "user", "content": content},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "survey_extraction",
                        "strict": True,
                        "schema": EXTRACTION_SCHEMA,
                    },
                },
            )
            return json.loads(response.choices[0].message.content)
        except Exception as error:
            last_error = error

    return {
        "question_summary": "", "response_items": [], "base_n": None,
        "unit": None, "multi_response": False, "prev_year_note": None,
        "extraction_confidence": "low", "warning": f"비전 호출 실패: {last_error}",
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = sys.argv[1:]
    if len(args) < 3:
        print('사용법: uv run python rag/extract_vision.py "<파일명.pdf>" <page_start> <page_end>')
        return
    source, ps, pe = args[0], int(args[1]), int(args[2])

    client = get_client()
    data = extract_pages_vision(client, source, ps, pe)
    items = data.get("response_items", [])
    print(f"📄 {source} p.{ps}-{pe} | 품목 {len(items)}개 | base_n={data.get('base_n')} | conf={data.get('extraction_confidence')}")
    for it in items:
        print(f"  {it.get('label')}: {it.get('value')}")
    if data.get("warning"):
        print(f"  ⚠️ {data.get('warning')}")


if __name__ == "__main__":
    main()
