# rag/curate/adjudicate.py
# -----------------------------------------------------------------------------
# LLM 검증(adjudication) 레이어 — 하이브리드 게이트의 '자동 확정' 절반.
#
# 목적:
#   게이트가 차단하는 '불확실' 행(빈칸·저신뢰·합계이상 등)을, LLM 이 원문 페이지를
#   **비전으로 독립 재판독**해 대조한다(추출은 텍스트였으니 모달리티를 바꿔 실패모드 분리).
#     - 원문이 추출값을 지지            → agree   → llm_verified(값 그대로 확정)
#     - 원문에 다른 값이 명확히 보임     → correct → llm_verified(원문값으로 확정)
#     - 원문에서 확인 불가/불명확        → uncertain → 확정하지 않고 사람에게 에스컬레이션
#
# 원칙("추측은 데이터가 아니다") 유지:
#   - 원문 근거로 대조한 것만 확정한다. uncertain 은 절대 값으로 쓰지 않는다(사람 몫).
#   - 확정은 corrections.jsonl 에 status=llm_verified + reviewer='llm:adjudicate' 로만 기록
#     (canonical CSV 는 건드리지 않음). 사람이 이후 재검수하면 최신 레코드가 이긴다.
#
# 실행:
#   uv run python -m rag.curate.adjudicate            # 소량(기본 5건) 시험
#   uv run python -m rag.curate.adjudicate 50         # 50건
#   RAG_FAKE_LLM=1 ...                                # 무료·결정적(전부 uncertain=에스컬레이션, 데이터 불변)
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import logging
import os
import sys

from rag.core.config import VISION_MODEL, LLM_MAX_WORKERS
from rag.core.logging_setup import setup_logging
from rag.core.paths import OUTPUT_DIR
from rag.ingest.extract import get_client
from rag.ingest.extract_vision import render_page_images, _resolve_pdf
from rag.curate import corrections
from rag.curate.validate import is_uncertain_high

logger = logging.getLogger("adjudicate")

REVIEW_QUEUE = OUTPUT_DIR / "review_queue.csv"

# 검증 결과 스키마(OpenAI Structured Outputs, strict).
_VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["agree", "correct", "uncertain"]},
        "value": {"type": ["number", "null"]},   # correct 일 때 원문에서 읽은 값
        "reason": {"type": "string"},
    },
    "required": ["verdict", "value", "reason"],
}

_SYSTEM = (
    "너는 설문조사 보고서의 원문 페이지 이미지와 '추출된 수치'를 대조하는 검증자다. "
    "오직 이미지에서 실제로 보이는 값만 근거로 판단한다. 이미지에서 해당 보기의 값을 "
    "찾을 수 없거나 불명확하면 반드시 verdict='uncertain' 으로 답한다. 절대 추측하지 마라."
)


def _num(v) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def load_candidates(limit: int | None = None) -> list[dict]:
    """ 게이트가 차단하는 '불확실 high' 검수 큐 행을 검증 대상으로 모은다(원문 대조 가능한 것만).
        불확실 판정은 validate.is_uncertain_high 단일 소스를 쓴다(게이트와 100% 일치). """
    if not REVIEW_QUEUE.exists():
        return []
    with open(REVIEW_QUEUE, encoding="utf-8-sig", newline="") as f:
        queue = list(csv.DictReader(f))
    reviewed = corrections.reviewed_keys()
    cands = [r for r in queue
             if is_uncertain_high(r, reviewed)
             and (r.get("source") or "").strip()
             and (r.get("page_start") or "").strip()]
    return cands[:limit] if limit else cands


def load_ungated_candidates() -> list[dict]:
    """ 검수 큐를 거치지 않은 불확실 행(side-channel) — 값은 있으나 경고/저신뢰이고 큐·확정에 없음.
        원문 대조로 검증해 색인 가능한 확정 사실로 만들거나 사람에게 남긴다. """
    from rag.retrieval import chunking
    rows = chunking.load_rows()
    reviewed = corrections.reviewed_keys()
    confirmed = {corrections.row_key(r) for r in corrections.load_corrections()}
    # 큐 파일이 없을 수도 있다(신규 작업 폴더) — open 전에 존재 확인(없으면 빈 집합).
    queued = set()
    if REVIEW_QUEUE.exists():
        with open(REVIEW_QUEUE, encoding="utf-8-sig", newline="") as f:
            queued = {corrections.row_key(r) for r in csv.DictReader(f)}
    out = []
    for r in rows:
        if _num(r.get("value")) is None:
            continue
        conf = (r.get("extraction_confidence") or "").strip().lower()
        warn = (r.get("warning") or "").strip()
        if not warn and conf not in ("low", "medium"):
            continue
        k = corrections.row_key(r)
        if k in reviewed or k in confirmed or k in queued:
            continue
        if (r.get("source") or "").strip() and (r.get("page_start") or "").strip():
            out.append(r)
    return out


def _user_prompt(row: dict) -> str:
    reasons = (row.get("review_reasons") or "").strip()
    return (
        f"[문항] {row.get('question_summary') or row.get('std_id')} "
        f"({row.get('year')}, {row.get('std_id')})\n"
        f"[보기(응답 라벨)] {row.get('std_response_label') or row.get('response_label')}\n"
        f"[추출된 값] {row.get('value')}{row.get('unit') or ''}\n"
        f"[플래그 사유] {reasons or '(없음)'}\n\n"
        "아래 페이지 이미지에서 이 보기의 값을 확인하라.\n"
        "- 원문이 추출값을 지지하면 verdict='agree'\n"
        "- 원문에 다른 값이 명확히 보이면 verdict='correct', value=<원문값(숫자)>\n"
        "- 원문에서 이 보기를 못 찾거나 불명확하면 verdict='uncertain'"
    )


def adjudicate_row(client, row: dict) -> dict:
    """ 한 행을 원문 페이지(비전)로 검증해 verdict dict 를 돌려준다.
        실패/이미지없음/FAKE → uncertain(에스컬레이션; 데이터 안 건드림). """
    # 테스트·검증 모드: 실제 호출 없이 항상 uncertain → 데이터 불변(추측 금지 유지).
    if os.getenv("RAG_FAKE_LLM"):
        return {"verdict": "uncertain", "value": None, "reason": "(FAKE) 에스컬레이션"}

    import base64
    try:
        pdf_path = _resolve_pdf(row.get("source", ""))
        ps = int(float(row.get("page_start")))
        pe = int(float(row.get("page_end") or ps))
        images = render_page_images(pdf_path, ps, pe)
    except Exception as error:
        logger.warning("이미지 렌더 실패 → uncertain: %s (%s)", row.get("source"), error)
        return {"verdict": "uncertain", "value": None, "reason": f"이미지 없음: {error}"}

    content = [{"type": "text", "text": _user_prompt(row)}]
    for png in images:
        b64 = base64.b64encode(png).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})
    try:
        resp = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": content}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "verdict", "strict": True,
                                             "schema": _VERDICT_SCHEMA}},
        )
        import json
        return json.loads(resp.choices[0].message.content)
    except Exception as error:
        logger.warning("검증 호출 실패 → uncertain: %s", error)
        return {"verdict": "uncertain", "value": None, "reason": f"호출 실패: {error}"}


def _apply_verdict(row: dict, verdict: dict) -> str:
    """ verdict 를 corrections.jsonl 에 반영한다. 반환: 'confirmed'|'corrected'|'escalated'. """
    v = verdict.get("verdict")
    reason = (verdict.get("reason") or "")[:200]
    if v == "agree":
        # 원문이 추출값을 지지 → 그 값 그대로 확정.
        corrections.add_correction(
            row, status=corrections.STATUS_LLM_VERIFIED,
            new_value=(row.get("value") or "").strip(),
            reviewer="llm:adjudicate", note=f"원문 대조 일치 — {reason}")
        return "confirmed"
    if v == "correct" and _num(verdict.get("value")) is not None:
        corrections.add_correction(
            row, status=corrections.STATUS_LLM_VERIFIED,
            new_value=str(verdict["value"]).strip(),
            reviewer="llm:adjudicate", note=f"원문값으로 교정 — {reason}")
        return "corrected"
    return "escalated"   # uncertain(또는 correct인데 값 없음) → 사람 몫, 아무것도 안 씀


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    setup_logging("adjudicate")

    limit = next((int(a) for a in sys.argv[1:] if a.isdigit()), 5)
    cands = load_candidates(limit)
    # 검수 큐를 거치지 않은 불확실 행(side-channel)도 검증 대상에 포함(중복 키 제외).
    # 합친 뒤에도 limit 을 지킨다 — UI 가 약속한 '최대 N건'(=과금 상한)을 넘지 않게.
    seen = {corrections.row_key(r) for r in cands}
    extras = [r for r in load_ungated_candidates() if corrections.row_key(r) not in seen]
    cands = (cands + extras)[:limit] if limit else cands + extras
    print(f"\n🤖 LLM 검증 대상 {len(cands)}건(불확실 high + side-channel) — 워커 {LLM_MAX_WORKERS}")
    logger.info("adjudicate 시작 — 대상 %d건 · 모델 %s", len(cands), VISION_MODEL)
    if not cands:
        print("검증할 항목이 없습니다.")
        return

    try:
        client = None if os.getenv("RAG_FAKE_LLM") else get_client()
    except RuntimeError as error:
        print(f"❌ {error}")
        return

    tally = {"confirmed": 0, "corrected": 0, "escalated": 0}
    for i, row in enumerate(cands, start=1):
        verdict = adjudicate_row(client, row)
        outcome = _apply_verdict(row, verdict)
        tally[outcome] += 1
        print(f"[{i}/{len(cands)}] {row.get('year')} {row.get('std_id')} · "
              f"{(row.get('std_response_label') or '')[:24]} → {verdict.get('verdict')} ({outcome})")

    print(f"\n결과: 확정 {tally['confirmed']} · 교정 {tally['corrected']} · "
          f"에스컬레이션(사람) {tally['escalated']}")
    logger.info("adjudicate 완료 — 확정 %d · 교정 %d · 에스컬레이션 %d",
                tally["confirmed"], tally["corrected"], tally["escalated"])


if __name__ == "__main__":
    main()
