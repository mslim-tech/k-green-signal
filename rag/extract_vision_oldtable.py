# rag/extract_vision_oldtable.py
# -----------------------------------------------------------------------------
# 옛 형식(2018~2022 계열) 보고서의 '응답자 특성별 교차분석 표'에서
# '전체(국민 전체)' 연도행을 비전으로 읽어 연도별 레코드로 만든다.
#
# 왜 필요한가:
#   - 옛 보고서는 Q.·서술형이 아니라 [그림](차트 이미지)·[표](교차분석)로 결과를 준다.
#     텍스트 추출은 [그림] 산문요약뿐이라 값이 안 나오고(파일럿에서 0개 확인),
#     실제 전체 수치는 [표]의 연도행([2022년] 등)에 있다(열이 뒤섞여 비전이 필요).
#   - 한 표에 [2019]~[2022] 여러 해 '전체' 행이 함께 있어, 비전 1콜로 여러 해를 얻는다.
#   - 성별/연령/지역 등 하위집단 행과 [TOP3]/[비인지]/소계 같은 집계 열은 제외한다
#     ('전체만' 스코프 + "추측은 데이터가 아니다": 명확히 보이는 전체 행만).
#
#   출력: extract.py 의 ExtractedRecord 와 같은 jsonl 레코드(연도는 '행'에서 가져옴).
#         → 이후 standardize→refine→dedup→flags→review→chunking→index 로 흐른다.
#
# 보안: API Key 는 .env 의 OPENAI_API_KEY 에서만(get_client 재사용).
#
# 실행:
#   uv run python rag/extract_vision_oldtable.py "<파일명.pdf>" [--save]
# -----------------------------------------------------------------------------

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

try:
    from rag.extract import get_client
    from rag.config import VISION_MODEL
    from rag.extract_vision import render_page_images, _resolve_pdf, extract_pages_vision
except ImportError:
    from extract import get_client
    from config import VISION_MODEL
    from extract_vision import render_page_images, _resolve_pdf, extract_pages_vision


try:
    from rag.paths import OUTPUT_DIR
except ImportError:
    from paths import OUTPUT_DIR

RE_TAB = re.compile(r"\[\s*표\s*\d+\s*[-–~]\s*\d+\s*\]\s*(.*)")   # [표 3-1] 제목
RE_YEARROW = re.compile(r"\[\s*20\d{2}\s*년\s*\]")               # [2022년] 전체 연도행
# 집계/머리글 라벨(응답 보기가 아님) — 비전이 섞어 넣으면 후처리로 제외
# 집계/머리글(응답 보기가 아님). '인지/비인지'는 척도형일 때만 집계라 여기 안 넣고
# _DERIVED 로 따로 처리한다(이진형 '인지/비인지' 단독 문항은 보존).
_AGG = ("top", "소계", "합계", "사례수", "구분")


# 표 페이지에서 비전이 채울 다년 구조. 연도행마다 보기 분포를 받는다.
MULTIYEAR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question_summary", "unit", "multi_response", "years"],
    "properties": {
        "question_summary": {"type": "string"},
        "unit": {"type": ["string", "null"]},
        "multi_response": {"type": "boolean"},
        "years": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["year", "base_n", "items"],
                "properties": {
                    "year": {"type": "integer"},
                    "base_n": {"type": ["integer", "null"]},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["label", "value"],
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": ["number", "null"]},
                            },
                        },
                    },
                },
            },
        },
    },
}


VISION_SYSTEM = (
    "너는 설문 결과보고서의 '응답자 특성별 교차분석 표' 이미지에서 '전체(국민 전체)' "
    "기준의 '연도별 행'만 정확히 읽어 구조화하는 도구다.\n"
    "표 구조: 맨 위에 [2019년]·[2020년]·…·[2022년] 같은 '연도 행'이 있고(=각 해의 전체 값), "
    "그 아래에 성별/연령/지역/직업/가구 등 '하위집단 행'이 있다.\n"
    "규칙:\n"
    "- '연도 행'만 읽어라. 성별/연령/지역/직업/가구소득 등 하위집단 행은 절대 넣지 마라.\n"
    "- 각 연도 행에 대해, 표의 '열 머리글(응답 보기)'을 label 로, 그 칸의 값을 value 로 짝지어라.\n"
    "- '사례수', '[TOP3]', '[TOP2]', '[비인지]', '소계', '합계', '계', '구분' 같은 "
    "집계·머리글 열은 응답 보기가 아니므로 items 에 넣지 마라. (사례수는 base_n 으로.)\n"
    "- 값이 '-' 이거나 비어 있으면 그 해 그 칸은 value 를 null 로. 값을 지어내지 마라.\n"
    "- year 는 [YYYY년] 의 4자리 정수. unit 은 보통 '%'. 복수응답 표기가 있으면 multi_response=true.\n"
    "- 모든 텍스트는 한국어."
)


def find_table_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """ 연도행이 있는 교차분석 표 페이지(1-based)와 표 제목 목록. """
    doc = fitz.open(pdf_path)
    out: list[tuple[int, str]] = []
    for i in range(doc.page_count):
        t = doc[i].get_text("text")
        if RE_YEARROW.search(t):
            m = RE_TAB.search(t)
            if m:
                out.append((i + 1, m.group(1).strip()))
    doc.close()
    return out


def _is_agg(label: str) -> bool:
    s = (label or "").strip().lower()
    return (not s) or any(a in s for a in _AGG)


# 척도형 문항의 보기(이게 있으면 '인지'/'비인지'는 파생 집계열이라 제외)
_SCALE = ("잘 알고", "조금 알고", "본 적", "전혀 모", "처음 들", "처음 본", "들어 본")
_DERIVED = ("인지", "비인지")   # 척도형에서 [인지](=TOP합)·[비인지] 파생 집계열


def _clean_items(items: list[dict]) -> list[dict]:
    """ 보기 목록 정리: 집계·머리글 제외, 중복 라벨 제거, 척도형의 파생 집계('인지'/
        '비인지') 제외. 척도 보기(잘 알고…)가 있을 때만 '인지/비인지'를 집계로 본다
        (이진형 '인지/비인지' 단독 문항은 보존). """
    has_scale = any(any(k in (it.get("label") or "") for k in _SCALE) for it in items)
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        label = (it.get("label") or "").strip()
        value = it.get("value")
        if _is_agg(label) or value is None:
            continue
        if has_scale and label in _DERIVED:      # 척도형의 파생 집계열 → 제외
            continue
        if label in seen:                         # 중복 라벨 → 제외
            continue
        seen.add(label)
        out.append({"label": label, "value": value})
    return out


def extract_table_page(client, pdf_path: Path, pageno: int, title: str,
                       model: str = VISION_MODEL, retries: int = 2) -> dict:
    """ 한 표 페이지를 비전으로 읽어 다년 구조(dict)를 돌려준다. """
    images = render_page_images(pdf_path, pageno, pageno)
    user_text = (
        "다음 표 이미지에서 '연도별 전체 행'만 읽어줘. 하위집단(성별/연령 등) 행은 제외.\n"
        f"[표 제목] {title or '(없음)'}\n"
    )
    content = [{"type": "text", "text": user_text}]
    for png in images:
        b64 = base64.b64encode(png).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})

    last_error = None
    for _ in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0,
                messages=[{"role": "system", "content": VISION_SYSTEM},
                          {"role": "user", "content": content}],
                response_format={"type": "json_schema", "json_schema": {
                    "name": "oldtable_multiyear", "strict": True,
                    "schema": MULTIYEAR_SCHEMA}},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as error:
            last_error = error
    return {"question_summary": "", "unit": None, "multi_response": False,
            "years": [], "warning": f"비전 호출 실패: {last_error}"}


def to_records(result: dict, source: str, pageno: int, title: str) -> list[dict]:
    """ 비전 다년 결과 → extract.py ExtractedRecord 형식의 연도별 레코드들. """
    summary = (result.get("question_summary") or title).strip()
    unit = result.get("unit")
    multi = bool(result.get("multi_response"))
    records: list[dict] = []
    for ye in result.get("years", []):
        year = ye.get("year")
        items = _clean_items(ye.get("items", []))   # 집계·중복·파생 집계 제외
        if not year or not items:
            continue
        records.append({
            "source": source, "year": int(year),
            "page_start": pageno, "page_end": pageno,
            "section": None, "subsection": title or None,
            "question_summary": summary, "response_items": items,
            "base_n": ye.get("base_n"), "unit": unit, "multi_response": multi,
            "prev_year_note": None, "figures": [],
            "extraction_confidence": "high", "warning": None,
        })
    return records


def run(source: str, save: bool = False) -> list[dict]:
    """ PDF 한 개의 모든 표 페이지를 비전 추출해 레코드 목록을 만든다(옵션: jsonl 저장). """
    pdf_path = _resolve_pdf(source)
    client = get_client()
    pages = find_table_pages(pdf_path)
    print(f"📄 {pdf_path.name} | 표 페이지 {len(pages)}개")
    all_records: list[dict] = []
    for n, (pageno, title) in enumerate(pages, 1):
        result = extract_table_page(client, pdf_path, pageno, title)
        recs = to_records(result, pdf_path.name, pageno, title)
        all_records.extend(recs)
        yrs = sorted({r["year"] for r in recs})
        print(f"  [{n}/{len(pages)}] p.{pageno} {title[:30]} → {len(recs)}행 {yrs}")

    if save:
        out = OUTPUT_DIR / f"{pdf_path.stem}.extracted.jsonl"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            for r in all_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"💾 저장: {out} ({len(all_records)}개 레코드)")
    return all_records


# --- 2014~2017 계열: 연도행이 없고 '응답자 특성별 교차분석'의 '전체' 행만 있는 형식 ---
# 각 문항: 차트 페이지(제목+서술) → 교차분석 표(전체/성별/연령). 표의 '전체' 행이 그 해
# 전체값. 차트+표를 함께 비전에 줘 제목과 완전한 전체 분포를 읽는다(연도는 파일명).
import re as _re

RE_CROSSTAB = _re.compile(r"응답자\s*특성[에별].*교차분석")


def _year_from_name(name: str) -> int | None:
    m = _re.search(r"(20\d{2})", name)
    return int(m.group(1)) if m else None


RE_Q = _re.compile(r"Q\s*[.．]")


def find_crosstab_pages(pdf_path: Path) -> list[int]:
    """ '응답자 특성별 교차분석' 표 페이지(1-based) 중, 직전 페이지가 'Q.' 차트인 것만.
        → 문항당 주(1차) 표 하나만 잡아 제목·값을 정확히(복수응답 보조표는 직전이
        1순위 표라 값이 섞이므로 제외). """
    doc = fitz.open(pdf_path)
    out = []
    for i in range(doc.page_count):
        if RE_CROSSTAB.search(doc[i].get_text("text")):
            prev = doc[i - 1].get_text("text") if i >= 1 else ""
            if RE_Q.search(prev):          # 직전이 차트(Q.)인 표만
                out.append(i + 1)
    doc.close()
    return out


def run_totalrow(source: str, save: bool = False, limit: int | None = None) -> list[dict]:
    """ 2014~2017 형식: 교차분석 표의 '전체' 행을 비전으로 읽어 그 해 레코드를 만든다.
        차트(직전 페이지)+표를 함께 줘 문항 제목과 전체 분포를 얻는다. """
    pdf_path = _resolve_pdf(source)
    year = _year_from_name(pdf_path.name)
    client = get_client()
    pages = find_crosstab_pages(pdf_path)
    if limit:
        pages = pages[:limit]
    print(f"📄 {pdf_path.name} | 교차분석(전체행) 페이지 {len(pages)}개 | year={year}")
    records: list[dict] = []
    for n, pageno in enumerate(pages, 1):
        ps = max(1, pageno - 1)   # 차트(제목)+표 함께
        d = extract_pages_vision(
            client, source, ps, pageno,
            context="이 문항의 제목과, '응답자 특성별 교차분석' 표의 '전체' 행 응답 분포를 "
                    "읽어라. 성별/연령/지역/직업 등 하위집단 행은 제외.")
        items = _clean_items(d.get("response_items", []))
        summary = (d.get("question_summary") or "").strip()
        if not items or year is None:
            print(f"  [{n}/{len(pages)}] p.{pageno} → (값 없음/스킵)")
            continue
        records.append({
            "source": pdf_path.name, "year": year,
            "page_start": pageno, "page_end": pageno,
            "section": None, "subsection": summary,
            "question_summary": summary, "response_items": items,
            "base_n": d.get("base_n"), "unit": d.get("unit"),
            "multi_response": bool(d.get("multi_response")),
            "prev_year_note": None, "figures": [],
            "extraction_confidence": d.get("extraction_confidence") or "high",
            "warning": d.get("warning"),
        })
        print(f"  [{n}/{len(pages)}] p.{pageno} {summary[:34]} → {len(items)}보기")
    if save:
        out = OUTPUT_DIR / f"{pdf_path.stem}.extracted.jsonl"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"💾 저장: {out} ({len(records)}개 레코드)")
    return records


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = sys.argv[1:]
    save = "--save" in args
    totalrow = "--totalrow" in args        # 2014~2017 형식(전체 행)
    args = [a for a in args if a not in ("--save", "--totalrow")]
    if not args:
        print('사용법: uv run python rag/extract_vision_oldtable.py "<파일명.pdf>" [--save] [--totalrow]')
        return
    (run_totalrow if totalrow else run)(args[0], save=save)


if __name__ == "__main__":
    main()
