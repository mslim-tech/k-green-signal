# rag/ingest/extract.py
# -----------------------------------------------------------------------------
# 2단계: LLM 구조화 추출 (LLM Structured Extraction)
#
# 이 파일의 역할:
#   - 1단계(parsing.py)가 잘라낸 "문항 블록"의 원문(raw_text)을 LLM(gpt-4o)에 주고,
#     연도/형식에 상관없이 "전체(국민 전체) 기준 핵심 수치"를 구조화해서 뽑는다.
#   - 보고서마다 메타 표기가 제각각((N=…) / [BASE…] / <표>+숫자나열)이라
#     정규식으로 다 쫓기 어렵다. 그래서 추출은 LLM 에게 맡긴다.
#   - 환각(없는 값 지어내기)을 막기 위해 OpenAI Structured Outputs(json_schema, strict)
#     로 출력 형식을 강제하고, 원문에 없으면 비우도록 지시한다.
#
#   사용자가 "우선 전체 핵심수치만" 을 선택했으므로, 성별/연령 등 하위집단 수치는
#   넣지 않고 '전체' 응답 분포만 뽑는다. (하위집단은 추후 단계)
#
# 보안: API Key 는 .env 의 OPENAI_API_KEY 에서만 읽는다.
#
# 실행 방법:
#   uv run python -m rag.ingest.extract                 # 2025 보고서에서 3개 블록만 시험 추출
#   uv run python -m rag.ingest.extract 파일.pdf 5       # 특정 파일에서 5개 블록 시험 추출
#   uv run python -m rag.ingest.extract 파일.pdf 999 --save   # 전체 추출 후 outputs/ 에 저장
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# 같은 폴더에서 실행하든 프로젝트 루트에서 실행하든 import 가 되도록 한다.
from rag.ingest.parsing import parse_pdf, QuestionBlock
from rag.core.config import EXTRACT_MODEL, LLM_MAX_WORKERS
from rag.core.logging_setup import setup_logging
logger = logging.getLogger("extract")


# 사용할 모델은 중앙 설정(config.py)에서 가져온다. (현재 gpt-5.4-mini)
MODEL_NAME = EXTRACT_MODEL

# 블록별 LLM 추출을 병렬로(refine 과 동일한 방식). 워커 수는 config 중앙관리(429 회피).
MAX_WORKERS = LLM_MAX_WORKERS


# -----------------------------------------------------------------------------
# 1) 출력 스키마 (OpenAI Structured Outputs, strict 모드)
#    - strict 모드에서는 모든 property 가 required 이고 additionalProperties=false 여야 한다.
#    - "값이 없을 수 있는" 항목은 타입에 "null" 을 함께 허용한다.
# -----------------------------------------------------------------------------
EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "question_summary",
        "response_items",
        "base_n",
        "unit",
        "multi_response",
        "prev_year_note",
        "extraction_confidence",
        "warning",
    ],
    "properties": {
        # 문항을 짧고 표준적인 한 문장으로 정리한 표현
        "question_summary": {"type": "string"},
        # 전체(국민 전체) 기준 응답 분포. 라벨과 수치.
        "response_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "value"],
                "properties": {
                    "label": {"type": "string"},        # 응답 항목 이름 (예: "알고 있다")
                    "value": {"type": ["number", "null"]},  # 수치 (예: 68.9)
                },
            },
        },
        "base_n": {"type": ["integer", "null"]},   # 표본 수 (예: 1000)
        "unit": {"type": ["string", "null"]},      # 단위 (예: "%")
        "multi_response": {"type": "boolean"},     # 복수응답 문항인가?
        "prev_year_note": {"type": ["string", "null"]},  # 전년 대비 변화 언급 (없으면 null)
        # LLM 자기평가: 추출이 얼마나 확실한가 (사람 검수 우선순위 판단용)
        "extraction_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "warning": {"type": ["string", "null"]},   # 추출이 애매했던 점
    },
}


SYSTEM_PROMPT = (
    "너는 설문조사 결과보고서에서 '전체(국민 전체) 기준' 핵심 통계를 구조화하는 도구다.\n"
    "주어진 '문항 블록' 원문에서 다음을 뽑아라:\n"
    "1) question_summary: 이 문항이 무엇을 묻는지 짧고 표준적인 한 문장으로.\n"
    "2) response_items: '전체' 응답 분포(라벨, 수치). 원문 문장에 적힌 전체 기준 퍼센트/점수를 사용한다.\n"
    "3) base_n: 표본 수(정수). 원문의 N=, n=, BASE 표기에서.\n"
    "4) unit: 단위(예: '%', '점').\n"
    "5) multi_response: 복수응답 문항이면 true.\n"
    "6) prev_year_note: 전년 대비 변화 언급이 있으면 짧게, 없으면 null.\n\n"
    "규칙:\n"
    "- '전체' 기준 수치만 뽑는다. 성별/연령/지역 등 하위집단 수치는 넣지 마라.\n"
    "- 원문에 없는 값은 절대 지어내지 마라. 없으면 null 또는 빈 배열로 둔다.\n"
    "- 표 숫자가 뒤섞여 라벨과 값을 확실히 짝지을 수 없으면, 억지로 채우지 말고 "
    "extraction_confidence 를 'low' 로 낮추고 warning 에 이유를 적어라.\n"
    "- 모든 텍스트는 한국어로."
)


# -----------------------------------------------------------------------------
# 2) 최종 레코드 (출처 정보 + LLM 추출 결과를 합친 것)
#    - 출처(source/page/연도)는 우리가 이미 아는 값이므로 LLM 에 맡기지 않고 직접 붙인다.
# -----------------------------------------------------------------------------
@dataclass
class ExtractedRecord:
    source: str
    year: int | None
    page_start: int
    page_end: int
    section: str | None
    subsection: str | None
    question_summary: str
    response_items: list[dict]
    base_n: int | None
    unit: str | None
    multi_response: bool
    prev_year_note: str | None
    figures: list[str]
    extraction_confidence: str
    warning: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def get_client() -> OpenAI:
    """ .env 에서 OPENAI_API_KEY 를 읽어 OpenAI 클라이언트를 만든다. """
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY 를 찾을 수 없습니다. 프로젝트 폴더의 .env 에 "
            "OPENAI_API_KEY=sk-... 한 줄을 넣어주세요."
        )
    return OpenAI(api_key=api_key)


def _year_from_source(source: str) -> int | None:
    """ 파일명 앞의 4자리 연도를 뽑는다. (예: '2025년 ...' -> 2025) """
    m = re.search(r"(20\d{2})", source)
    return int(m.group(1)) if m else None


def _build_user_prompt(block: QuestionBlock) -> str:
    """ LLM 에 줄 입력. 맥락(섹션/문항명)과 규칙기반 추정값(참고용)을 함께 준다. """
    hint = (
        f"규칙기반 추정(참고용, 틀릴 수 있음): "
        f"N={block.base_n}, 단위={block.unit}, 복수응답={block.multi_response}"
    )
    return (
        f"[대분류] {block.section or '(없음)'}\n"
        f"[문항명] {block.subsection or '(없음)'}\n"
        f"[{hint}]\n\n"
        f"[문항 블록 원문]\n{block.raw_text}"
    )


def _call_llm(client: OpenAI, user_prompt: str, model: str, retries: int = 2) -> dict:
    """ LLM 을 호출해 스키마에 맞는 dict 를 받는다. 실패하면 안전한 기본값을 돌려준다. """
    # 테스트/검증 모드: 실제 LLM 호출 없이 즉시 결정적 스텁 반환(무료·빠름).
    if os.getenv("RAG_FAKE_LLM"):
        return {
            "question_summary": "(테스트 스텁) 문항 요약",
            "response_items": [{"label": "테스트응답", "value": 100.0}],
            "base_n": 1000, "unit": "%", "multi_response": False,
            "prev_year_note": None, "extraction_confidence": "high",
            "warning": None,
        }

    last_error = None
    for _ in range(retries):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0,  # 같은 입력엔 같은 결과가 나오도록
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
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

    # 재시도까지 실패하면 빈 결과 + 경고로 돌려준다. (전체 파이프라인이 멈추지 않도록)
    return {
        "question_summary": "",
        "response_items": [],
        "base_n": None,
        "unit": None,
        "multi_response": False,
        "prev_year_note": None,
        "extraction_confidence": "low",
        "warning": f"LLM 호출 실패: {last_error}",
    }


def extract_block(client: OpenAI, block: QuestionBlock, model: str = MODEL_NAME) -> ExtractedRecord:
    """ 문항 블록 하나를 LLM 으로 구조화해서 최종 레코드로 만든다. """
    data = _call_llm(client, _build_user_prompt(block), model)
    return ExtractedRecord(
        source=block.source,
        year=_year_from_source(block.source),
        page_start=block.page_start,
        page_end=block.page_end,
        section=block.section,
        subsection=block.subsection,
        question_summary=data.get("question_summary", ""),
        response_items=data.get("response_items", []),
        base_n=data.get("base_n"),
        unit=data.get("unit"),
        multi_response=bool(data.get("multi_response", False)),
        prev_year_note=data.get("prev_year_note"),
        figures=block.figures,
        extraction_confidence=data.get("extraction_confidence", "low"),
        warning=data.get("warning"),
    )


# -----------------------------------------------------------------------------
# 3) 사람이 눈으로 확인하기 위한 출력 / 저장
# -----------------------------------------------------------------------------
def format_record(rec: ExtractedRecord) -> str:
    lines: list[str] = []
    span = f"p.{rec.page_start}" if rec.page_start == rec.page_end else f"p.{rec.page_start}-{rec.page_end}"
    lines.append(f"  [{rec.year} {span}] {rec.section or ''} > {rec.subsection or ''}")
    lines.append(f"    문항요약: {rec.question_summary}")
    meta = f"N={rec.base_n} | 단위={rec.unit} | 복수응답={'예' if rec.multi_response else '아니오'} | 신뢰도={rec.extraction_confidence}"
    lines.append(f"    메타: {meta}")
    if rec.response_items:
        items = ", ".join(
            f"{it.get('label')}={it.get('value')}" for it in rec.response_items
        )
        lines.append(f"    전체응답: {items}")
    else:
        lines.append("    전체응답: (없음)")
    if rec.prev_year_note:
        lines.append(f"    전년대비: {rec.prev_year_note}")
    if rec.warning:
        lines.append(f"    ⚠️ {rec.warning}")
    return "\n".join(lines)


def save_jsonl(records: list[ExtractedRecord], source_name: str) -> Path:
    """ 추출 결과를 outputs/ 에 JSONL 로 저장한다. (결과 파일이 필요해진 시점에 폴더 생성) """
    from rag.core.paths import OUTPUT_DIR as out_dir
    out_dir.mkdir(exist_ok=True)
    stem = Path(source_name).stem
    out_path = out_dir / f"{stem}.extracted.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
    return out_path


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = sys.argv[1:]
    do_save = "--save" in args
    args = [a for a in args if a != "--save"]

    # 파일 인자(비숫자)는 '여러 개' 올 수 있다 = 그 PDF 전부 추출. 개수(숫자)는 블록 상한.
    targets = [a for a in args if not a.isdigit()]
    count = next((int(a) for a in args if a.isdigit()), 3)

    if not targets:
        # 기본값: 2025 보고서에서 3개 블록만 시험 추출 (비용/시간 절약)
        targets = ["data/2025년 친환경생활·소비 국민 인지도 조사 결과보고서.pdf"]

    setup_logging("extract")   # 구조화 로그(시작·집계·에러)를 run 로그·파일에 남긴다.

    try:
        client = get_client()
    except RuntimeError as error:
        print(f"❌ {error}")
        logger.error("extract 중단 — OpenAI 클라이언트 생성 실패: %s", error)
        return

    # 선택된 PDF 를 순서대로 추출·저장한다(각 PDF → 별도 *.extracted.jsonl).
    for t_i, target in enumerate(targets, start=1):
        blocks = parse_pdf(target)
        selected = blocks[:count]
        print(f"\n📄 [{t_i}/{len(targets)}] {Path(target).name}")
        print(f"   전체 {len(blocks)}개 블록 중 {len(selected)}개를 {MODEL_NAME} 로 추출합니다...\n")
        logger.info("extract 시작 — %s · 전체 %d블록 중 %d블록 · 모델 %s · save=%s",
                    Path(target).name, len(blocks), len(selected), MODEL_NAME, do_save)

        # 블록을 병렬로 추출한다(순서는 idx 로 보존 — 저장 jsonl 은 원래 블록 순서).
        slots: list[ExtractedRecord | None] = [None] * len(selected)
        done_n = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut_to_idx = {ex.submit(extract_block, client, b): idx
                          for idx, b in enumerate(selected)}
            for fut in as_completed(fut_to_idx):
                idx = fut_to_idx[fut]
                rec = fut.result()
                slots[idx] = rec
                done_n += 1
                print(f"[{done_n}/{len(selected)}]")   # 완료 카운터(진행바 파싱용)
                print(format_record(rec))
                print()
        records: list[ExtractedRecord] = [r for r in slots if r is not None]

        # 신뢰도 요약 (사람 검수가 필요한 블록 파악용)
        conf = {"high": 0, "medium": 0, "low": 0}
        for rec in records:
            conf[rec.extraction_confidence] = conf.get(rec.extraction_confidence, 0) + 1
        print(f"신뢰도: high={conf['high']} / medium={conf['medium']} / low={conf['low']}")

        if do_save:
            out_path = save_jsonl(records, Path(target).name)
            print(f"💾 저장: {out_path}")
            logger.info("extract 완료 — %s · %d레코드(high %d / medium %d / low %d) 저장 %s",
                        Path(target).name, len(records),
                        conf["high"], conf["medium"], conf["low"], out_path.name)
        else:
            logger.info("extract 완료(미저장) — %s · %d레코드(high %d / medium %d / low %d)",
                        Path(target).name, len(records),
                        conf["high"], conf["medium"], conf["low"])


if __name__ == "__main__":
    main()
