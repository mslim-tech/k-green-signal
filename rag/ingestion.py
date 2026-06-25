# rag/ingestion.py
# -----------------------------------------------------------------------------
# 0단계: 문서 진단 (Document Diagnosis)
#
# 이 파일의 역할:
#   - data/ 폴더의 조사 보고서 PDF들을 "파싱하기 전에" 먼저 구조를 진단한다.
#   - 각 PDF가 디지털 텍스트인지 / 스캔 이미지인지 / 섞여 있는지 판별하고,
#     표(table) 추출이 얼마나 가능한지, 어떤 페이지가 위험한지(경고) 알려준다.
#   - 이 진단 결과를 보고 다음 단계(비정형 통계 파싱) 전략을 정한다.
#
# 아직 데이터를 추출/구조화하지 않는다. "이 PDF를 어떻게 다뤄야 하나"만 판단한다.
#
# 실행 방법:
#   uv run python rag/ingestion.py            # data/ 폴더 전체 진단
#   uv run python rag/ingestion.py 파일.pdf    # 특정 파일만 진단
# -----------------------------------------------------------------------------

from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import fitz  # PyMuPDF


# 한 페이지에서 뽑은 텍스트가 이 글자 수보다 적으면 "텍스트가 거의 없다"고 본다.
# (스캔 이미지 페이지는 보통 글자가 0~몇 개 수준이다.)
SCANNED_TEXT_THRESHOLD = 50

# 문서 전체에서 "스캔으로 의심되는 페이지" 비율에 따라 parser_type 을 정한다.
SCANNED_RATIO_FULL = 0.5   # 절반 이상이 스캔 → 문서 전체를 스캔본으로 간주
SCANNED_RATIO_MIXED = 0.1  # 10% 이상이 스캔 → 섞임(mixed)


@dataclass
class PageDiagnosis:
    """ 한 페이지의 진단 결과. """
    page: int            # 페이지 번호 (1부터)
    char_count: int      # 추출된 텍스트 글자 수(공백 제외)
    image_count: int     # 페이지에 들어있는 이미지 개수
    table_count: int     # 감지된 표 개수
    looks_scanned: bool  # 스캔 이미지로 의심되는가?


@dataclass
class DocDiagnosis:
    """ PDF 한 개의 종합 진단 결과. """
    source: str                 # 파일명
    num_pages: int              # 전체 페이지 수
    parser_type: str            # 추천 처리 방식: digital-text / mixed / scanned-needs-ocr
    total_chars: int            # 문서 전체 텍스트 글자 수
    total_images: int           # 문서 전체 이미지 수
    total_tables: int           # 문서 전체에서 감지된 표 수
    scanned_pages: list[int]    # 스캔 의심 페이지 번호 목록
    warnings: list[str]         # 사람이 읽을 경고 메시지
    pages: list[PageDiagnosis] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _count_tables(page) -> int:
    """ 한 페이지에서 감지되는 표 개수를 센다. (실패해도 0으로 안전 처리) """
    try:
        return len(page.find_tables().tables)
    except Exception:
        # 일부 페이지/버전에서 표 탐지가 실패할 수 있다. 진단을 멈추지 않는다.
        return 0


def diagnose_pdf(path: str | Path, detect_tables: bool = True) -> DocDiagnosis:
    """
    PDF 한 개를 열어 페이지별로 텍스트/이미지/표를 살펴보고 종합 진단을 돌려준다.
    - detect_tables=False 로 두면 표 탐지를 건너뛰어 더 빠르게 진단한다.
    """
    path = Path(path)
    doc = fitz.open(path)

    pages: list[PageDiagnosis] = []
    total_chars = 0
    total_images = 0
    total_tables = 0
    scanned_pages: list[int] = []

    for index, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        char_count = len(text.strip())
        image_count = len(page.get_images())

        # 글자가 거의 없는데 이미지가 있으면 스캔 페이지로 의심한다.
        looks_scanned = char_count < SCANNED_TEXT_THRESHOLD and image_count > 0

        # 표 탐지는 텍스트가 있는 페이지에서만 의미가 있으므로,
        # 스캔 의심 페이지는 건너뛰어 속도를 아낀다.
        if detect_tables and not looks_scanned:
            table_count = _count_tables(page)
        else:
            table_count = 0

        pages.append(
            PageDiagnosis(
                page=index,
                char_count=char_count,
                image_count=image_count,
                table_count=table_count,
                looks_scanned=looks_scanned,
            )
        )

        total_chars += char_count
        total_images += image_count
        total_tables += table_count
        if looks_scanned:
            scanned_pages.append(index)

    num_pages = doc.page_count
    doc.close()

    parser_type = _decide_parser_type(len(scanned_pages), num_pages)
    warnings = _build_warnings(
        num_pages=num_pages,
        total_chars=total_chars,
        total_tables=total_tables,
        scanned_pages=scanned_pages,
        parser_type=parser_type,
    )

    return DocDiagnosis(
        source=path.name,
        num_pages=num_pages,
        parser_type=parser_type,
        total_chars=total_chars,
        total_images=total_images,
        total_tables=total_tables,
        scanned_pages=scanned_pages,
        warnings=warnings,
        pages=pages,
    )


def _decide_parser_type(scanned_count: int, num_pages: int) -> str:
    """ 스캔 의심 페이지 비율을 보고 문서 처리 방식을 추천한다. """
    if num_pages == 0:
        return "empty"
    ratio = scanned_count / num_pages
    if ratio >= SCANNED_RATIO_FULL:
        return "scanned-needs-ocr"
    if ratio >= SCANNED_RATIO_MIXED:
        return "mixed"
    return "digital-text"


def _build_warnings(
    num_pages: int,
    total_chars: int,
    total_tables: int,
    scanned_pages: list[int],
    parser_type: str,
) -> list[str]:
    """ 진단 결과에서 사람이 챙겨봐야 할 경고들을 만든다. """
    warnings: list[str] = []

    if num_pages == 0:
        warnings.append("페이지가 없습니다. 파일이 비었거나 손상되었을 수 있습니다.")
        return warnings

    if parser_type == "scanned-needs-ocr":
        warnings.append(
            f"스캔 이미지로 보입니다(스캔 의심 {len(scanned_pages)}/{num_pages}쪽). "
            "표/수치를 뽑으려면 OCR 단계가 추가로 필요합니다."
        )
    elif parser_type == "mixed":
        warnings.append(
            f"디지털 텍스트와 스캔이 섞여 있습니다(스캔 의심 {len(scanned_pages)}/{num_pages}쪽). "
            "해당 페이지만 따로 OCR 처리할지 검토가 필요합니다."
        )

    if total_chars == 0:
        warnings.append("문서 전체에서 추출된 텍스트가 0자입니다. (완전한 스캔본일 가능성)")

    if total_tables == 0 and parser_type != "scanned-needs-ocr":
        warnings.append(
            "표가 한 개도 감지되지 않았습니다. 통계가 그림/이미지로만 들어있거나, "
            "표 구조가 특이해 자동 탐지가 안 될 수 있습니다."
        )

    return warnings


def format_report(diag: DocDiagnosis) -> str:
    """ 진단 결과를 사람이 읽기 좋은 한 덩어리 텍스트로 만든다. """
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"📄 {diag.source}")
    lines.append("-" * 70)
    lines.append(f"  페이지 수      : {diag.num_pages}쪽")
    lines.append(f"  추천 처리방식  : {diag.parser_type}")
    lines.append(f"  추출 텍스트    : {diag.total_chars:,}자")
    lines.append(f"  이미지 수      : {diag.total_images:,}개")
    lines.append(f"  감지된 표      : {diag.total_tables:,}개")

    if diag.scanned_pages:
        preview = ", ".join(str(p) for p in diag.scanned_pages[:15])
        suffix = " ..." if len(diag.scanned_pages) > 15 else ""
        lines.append(f"  스캔 의심 쪽   : {len(diag.scanned_pages)}쪽 ({preview}{suffix})")

    if diag.warnings:
        lines.append("  ⚠️ 경고:")
        for w in diag.warnings:
            lines.append(f"     - {w}")
    else:
        lines.append("  ✅ 특이 경고 없음")

    return "\n".join(lines)


def _collect_pdf_paths(target: str | None) -> list[Path]:
    """ 진단할 PDF 경로 목록을 모은다. 인자가 없으면 data/ 폴더 전체를 본다. """
    if target:
        p = Path(target)
        if p.is_dir():
            return sorted(p.glob("*.pdf"))
        return [p]
    return sorted(Path("data").glob("*.pdf"))


def main() -> None:
    # 한글 윈도우 콘솔/파일(cp949)에서도 한글·이모지가 깨지지 않도록 UTF-8로 출력한다.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    target = sys.argv[1] if len(sys.argv) > 1 else None
    paths = _collect_pdf_paths(target)

    if not paths:
        print("진단할 PDF를 찾지 못했습니다. data/ 폴더에 PDF를 넣어주세요.")
        return

    print(f"\n총 {len(paths)}개 PDF를 진단합니다...\n")
    for path in paths:
        try:
            diag = diagnose_pdf(path)
            print(format_report(diag))
        except Exception as error:
            print("=" * 70)
            print(f"📄 {path.name}")
            print(f"  ❌ 진단 실패: {error}")
    print("=" * 70)


if __name__ == "__main__":
    main()
