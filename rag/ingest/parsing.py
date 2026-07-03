# rag/ingest/parsing.py
# -----------------------------------------------------------------------------
# 1단계: 문항 블록 분리 (Question-Block Splitting)
#
# 이 파일의 역할:
#   - 진단에서 확인한 보고서 구조를 이용해, 본문 텍스트를 "문항 단위"로 자른다.
#   - 이 보고서들의 통계는 표가 아니라 (1) 서술형 문장 속 수치, (2) 차트 이미지에
#     들어있다. 그래서 표 추출이 아니라 "문항 블록"을 잘라내는 것이 1단계다.
#
#   한 문항 블록은 보통 다음 신호로 이루어진다:
#     섹션 제목(예: "3. 환경표지 인증제품 구매행동")
#     문항명     (예: "1) 환경표지 인증제품 구매의향")
#     Q. 질문문  (예: "Q. ... 구매하실 의향이 있습니까?")
#     ○ 결과 서술 (수치가 문장 안에 들어있음)
#     (N=1,000, 단위: %)  ← 표본 수 / 단위 / 복수응답 여부
#     <그림 3-8> ...      ← 차트 이미지 참조
#
#   아직 LLM 은 쓰지 않는다. "블록이 정확히 잘리는지" 눈으로 확인하는 단계다.
#   각 블록에는 출처(source, page) 를 붙여 둔다. (다음 단계 LLM 추출의 입력이 됨)
#
# 실행 방법:
#   uv run python -m rag.ingest.parsing                # data/ 전체를 블록으로 나눠 요약 출력
#   uv run python -m rag.ingest.parsing 파일.pdf        # 특정 파일만
#   uv run python -m rag.ingest.parsing 파일.pdf 5      # 샘플 블록 5개까지 자세히 출력
# -----------------------------------------------------------------------------

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path

import fitz  # PyMuPDF


# --- 문항 구조를 알려주는 신호(정규식) ---------------------------------------
# 섹션 제목:  "3. 환경표지 인증제품 구매행동"
RE_SECTION = re.compile(r"^\s*(\d+)\.\s+\S")
# 문항명:     "1) 환경표지 인증제품 구매의향"
RE_SUBSECTION = re.compile(r"^\s*(\d+)\)\s+\S")
# 질문문:     "Q. ..."  (Q 뒤에 점/전각점)
RE_QUESTION = re.compile(r"^\s*Q\s*[.．]")
# 메타 줄: 연도마다 표기가 다르다.
#   2024·2025: "(N=1,000, 단위: %)"
#   2023     : "[BASE : 전체 (n=1,000), 단위 : %, 복수응답]"
# 두 형식을 모두 잡는다. (BASE: 또는 괄호 안의 N=/n=)
RE_META = re.compile(r"BASE\s*[:：]|[\(\[]\s*[Nn]\s*=")
RE_NVALUE = re.compile(r"[Nn]\s*=\s*([\d,]*\d)")
RE_UNIT = re.compile(r"단위\s*[:：]\s*([^),\]]+)")
# 도표 참조:  "<그림 3-8> ..." 또는 "<표 3-1> ..." (연도마다 그림/표 혼용)
RE_FIGURE = re.compile(r"<(?:그림|표)\s*\d+\s*[-–~]\s*\d+>[^\n]*")
# 페이지 번호 줄: "18｜", "｜53", "53" 등 (｜ 는 전각 세로줄)
RE_PAGENUM = re.compile(r"^\s*\d{1,3}\s*[｜|]?\s*$|^\s*[｜|]\s*\d{1,3}\s*$")


@dataclass
class QuestionBlock:
    """ 잘라낸 문항 블록 하나. 다음 단계(LLM 추출)의 입력이 된다. """
    source: str                       # 출처 파일명
    page_start: int                   # 블록이 시작된 페이지
    page_end: int                     # 블록이 끝난 페이지
    section: str | None               # 대분류 섹션 제목
    subsection: str | None            # 문항명(중분류)
    question: str                     # Q. 질문문
    base_n: str | None                # 표본 수 (예: "1,000")
    unit: str | None                  # 단위 (예: "%")
    multi_response: bool              # 복수응답 문항인가?
    figures: list[str] = field(default_factory=list)  # <그림 ...> 참조들
    body: str = ""                    # ○ 결과 서술 등 본문
    raw_text: str = ""                # 블록 전체 원문(LLM 입력용)

    def to_dict(self) -> dict:
        return asdict(self)


# -----------------------------------------------------------------------------
# 1) PDF -> (페이지번호, 줄) 목록으로 펼치고, 머리말/꼬리말 잡음 제거
# -----------------------------------------------------------------------------
def _extract_lines(path: Path) -> list[tuple[int, str]]:
    """ PDF 를 열어 (페이지번호, 한 줄) 형태로 모든 줄을 펼친다. """
    doc = fitz.open(path)
    lines: list[tuple[int, str]] = []
    for pageno, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        for raw in text.splitlines():
            if raw.strip():
                lines.append((pageno, raw))
    doc.close()
    return lines


def _strip_noise(lines: list[tuple[int, str]], page_count: int) -> list[tuple[int, str]]:
    """
    매 페이지 반복되는 머리말/꼬리말과 페이지 번호 줄을 걸러낸다.
    - 페이지 번호 줄: 정규식으로 제거
    - 반복 머리말/꼬리말: 여러 페이지에 똑같이 나오는 짧은 줄을 제거
    """
    # 같은 줄이 몇 번 등장하는지 센다.
    freq = Counter(line.strip() for _, line in lines)
    # 전체 페이지의 30% 이상(최소 3회)에 반복되는 짧은 줄은 머리말/꼬리말로 본다.
    repeat_threshold = max(3, int(page_count * 0.3))

    cleaned: list[tuple[int, str]] = []
    for pageno, line in lines:
        s = line.strip()
        if RE_PAGENUM.match(s):
            continue
        if len(s) < 40 and freq[s] >= repeat_threshold:
            continue
        cleaned.append((pageno, line))
    return cleaned


# -----------------------------------------------------------------------------
# 2) 줄들을 문항 블록으로 묶기 (상태 기계)
#    - Q. 를 만나면 새 블록을 시작한다.
#    - 그 전에 본 섹션/문항명을 블록의 맥락으로 붙인다.
#    - 나머지 줄(○ 결과, 메타, 그림)은 현재 블록에 쌓는다.
# -----------------------------------------------------------------------------
def parse_pdf(path: str | Path) -> list[QuestionBlock]:
    """ PDF 한 개를 문항 블록 목록으로 변환한다. """
    path = Path(path)
    doc = fitz.open(path)
    page_count = doc.page_count
    doc.close()

    lines = _strip_noise(_extract_lines(path), page_count)

    blocks: list[QuestionBlock] = []
    section: str | None = None
    subsection: str | None = None

    # 현재 모으고 있는 블록의 임시 상태
    cur_question: str | None = None
    cur_page_start: int = 0
    cur_section: str | None = None
    cur_subsection: str | None = None
    cur_lines: list[tuple[int, str]] = []

    def flush():
        """ 지금까지 모은 블록을 완성해서 blocks 에 넣는다. """
        if cur_question is None:
            return
        block = _finalize_block(
            source=path.name,
            question=cur_question,
            page_start=cur_page_start,
            section=cur_section,
            subsection=cur_subsection,
            body_lines=cur_lines,
        )
        blocks.append(block)

    for pageno, line in lines:
        s = line.strip()

        if RE_QUESTION.match(s):
            # 새 문항 시작 → 직전 블록을 마감하고 새로 연다.
            flush()
            cur_question = s
            cur_page_start = pageno
            cur_section = section
            cur_subsection = subsection
            cur_lines = []
        elif RE_SECTION.match(s) and len(s) < 40:
            # 대분류 섹션 제목 갱신 (새 섹션이면 문항명은 초기화)
            section = s
            subsection = None
        elif RE_SUBSECTION.match(s) and len(s) < 60:
            # 문항명(중분류) 갱신
            subsection = s
        else:
            # 결과 서술/메타/그림 등 → 현재 블록에 쌓는다. (블록이 열려 있을 때만)
            if cur_question is not None:
                cur_lines.append((pageno, line))

    flush()
    return blocks


def _finalize_block(
    source: str,
    question: str,
    page_start: int,
    section: str | None,
    subsection: str | None,
    body_lines: list[tuple[int, str]],
) -> QuestionBlock:
    """ 모은 줄들에서 메타(N/단위/복수응답)와 그림 참조를 뽑아 블록을 완성한다. """
    page_end = body_lines[-1][0] if body_lines else page_start

    base_n: str | None = None
    unit: str | None = None
    multi_response = False
    figures: list[str] = []
    body_parts: list[str] = []

    for _, line in body_lines:
        s = line.strip()

        # 메타 줄: (N=1,000, 단위: %, 복수응답)
        if RE_META.search(s):
            m = RE_NVALUE.search(s)
            if m:
                base_n = m.group(1)
            u = RE_UNIT.search(s)
            if u:
                unit = u.group(1).strip()
            if "복수응답" in s:
                multi_response = True
            continue

        # 그림 참조 줄
        fig = RE_FIGURE.search(s)
        if fig:
            figures.append(fig.group(0).strip())
            continue

        body_parts.append(s)

    body = "\n".join(body_parts)
    raw_text = question + "\n" + "\n".join(line for _, line in body_lines)

    return QuestionBlock(
        source=source,
        page_start=page_start,
        page_end=page_end,
        section=section,
        subsection=subsection,
        question=question,
        base_n=base_n,
        unit=unit,
        multi_response=multi_response,
        figures=figures,
        body=body,
        raw_text=raw_text,
    )


# -----------------------------------------------------------------------------
# 3) 사람이 눈으로 확인하기 위한 출력
# -----------------------------------------------------------------------------
def format_block(block: QuestionBlock, body_chars: int = 300) -> str:
    """ 블록 하나를 사람이 읽기 좋게 요약한다. """
    lines: list[str] = []
    span = f"p.{block.page_start}" if block.page_start == block.page_end else f"p.{block.page_start}-{block.page_end}"
    lines.append(f"  [{span}] {block.section or '(섹션없음)'} > {block.subsection or '(문항명없음)'}")
    lines.append(f"    Q: {block.question}")
    meta = f"N={block.base_n or '?'} | 단위={block.unit or '?'} | 복수응답={'예' if block.multi_response else '아니오'}"
    lines.append(f"    메타: {meta}")
    if block.figures:
        lines.append(f"    그림: {' / '.join(block.figures)}")
    snippet = block.body[:body_chars].replace("\n", " ")
    if len(block.body) > body_chars:
        snippet += " ..."
    lines.append(f"    본문: {snippet}")
    return "\n".join(lines)


def _collect_pdf_paths(target: str | None) -> list[Path]:
    if target:
        p = Path(target)
        if p.is_dir():
            return sorted(p.glob("*.pdf"))
        return [p]
    return sorted(Path("data").glob("*.pdf"))


def main() -> None:
    # 한글 윈도우 콘솔/파일(cp949)에서도 깨지지 않도록 UTF-8로 출력한다.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = [a for a in sys.argv[1:]]
    target = args[0] if args and not args[0].isdigit() else None
    # 마지막 숫자 인자는 "샘플 몇 개를 자세히 볼지"
    sample = 3
    for a in args:
        if a.isdigit():
            sample = int(a)

    paths = _collect_pdf_paths(target)
    if not paths:
        print("PDF를 찾지 못했습니다. data/ 폴더를 확인하세요.")
        return

    for path in paths:
        try:
            blocks = parse_pdf(path)
        except Exception as error:
            print("=" * 72)
            print(f"📄 {path.name}\n  ❌ 분리 실패: {error}")
            continue

        # 메타가 얼마나 잡혔는지 간단 통계
        with_n = sum(1 for b in blocks if b.base_n)
        with_fig = sum(1 for b in blocks if b.figures)
        multi = sum(1 for b in blocks if b.multi_response)

        print("=" * 72)
        print(f"📄 {path.name}")
        print(f"  문항 블록 {len(blocks)}개  |  N 인식 {with_n}개  |  그림 참조 {with_fig}개  |  복수응답 {multi}개")
        print("-" * 72)
        for block in blocks[:sample]:
            print(format_block(block))
            print()
    print("=" * 72)


if __name__ == "__main__":
    main()
