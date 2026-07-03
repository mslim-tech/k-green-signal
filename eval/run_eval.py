# eval/run_eval.py
# -----------------------------------------------------------------------------
# RAG 회귀 평가 — '질문 → 기대 출처/항목'을 실제 LLM 경로로 돌려 정량 채점한다.
#
# 왜 필요한가:
#   - E2E 는 RAG_FAKE_LLM 이라 'UI 배선'만 증명한다. 실제 검색·리랭크·프롬프트가
#     맞는 출처를 찾아 맞는 항목을 답하는지는 증명하지 못한다.
#   - 이 평가셋(eval/questions.jsonl)은 각 질문의 정답을 PDF 원문(청크)에서 확인한
#     것만 담고, 답변이 그 정답에 grounding 되는지/순위 규칙을 지키는지 점수로 본다.
#     → rerank·프롬프트를 바꿨을 때 회귀를 숫자로 잡는다.
#
# 채점(질문별, 모두 통과해야 PASS):
#   1) grounding   : 검색 결과에 기대 연도 + 기대 출처(파일) 청크가 있는가
#   2) answer_has  : 답변에 기대 항목/수치 문자열이 모두 포함되는가
#   3) head_lacks  : 답변 첫 줄(1위 문장)에 집계·비응답 항목('기타'·'없음')이 없는가
#
# 전제: outputs/chroma 인덱스 + .env 의 OPENAI_API_KEY (실제 LLM 호출).
#
# 실행:  uv run python eval/run_eval.py
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EVAL_FILE = Path(__file__).resolve().parent / "questions.jsonl"


def load_cases() -> list[dict]:
    return [json.loads(line) for line in EVAL_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def grade_one(case: dict) -> dict:
    """ 한 질문을 실제로 답하게 하고 3개 기준으로 채점한다. 실패 사유도 모아 돌려준다. """
    from rag.retrieval.answer import answer

    res = answer(case["q"], k=5)
    text = res.text or ""
    head = text.split("\n", 1)[0]
    fails: list[str] = []

    # 1) grounding: 기대 연도 + 기대 출처 청크가 검색됐는가
    grounded = any(
        str(h.metadata.get("year")) == case["year"]
        and case["source"] in str(h.metadata.get("source", ""))
        for h in res.hits
    )
    if not grounded:
        got = {(str(h.metadata.get("year")), str(h.metadata.get("source", "")))
               for h in res.hits}
        fails.append(f"grounding: {case['year']}/{case['source']} 미검색 (검색됨: {got})")

    # 2) answer_has: 기대 항목/수치가 모두 답변에 있는가
    for term in case.get("answer_has", []):
        if term not in text:
            fails.append(f"answer_has: '{term}' 누락")

    # 3) head_lacks: 1위 문장에 집계·비응답 항목이 끼지 않았는가
    for term in case.get("head_lacks", []):
        if term in head:
            fails.append(f"head_lacks: 1위 문장에 '{term}' 포함")

    return {"id": case["id"], "passed": not fails, "fails": fails,
            "text": text, "truth": case.get("truth", "")}


def run() -> dict:
    cases = load_cases()
    results = [grade_one(c) for c in cases]
    passed = sum(1 for r in results if r["passed"])
    return {"total": len(results), "passed": passed,
            "score": round(passed / len(results), 3) if results else 0.0,
            "results": results}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    report = run()
    print(f"RAG 평가 — {report['passed']}/{report['total']} 통과 "
          f"(score {report['score']})\n" + "=" * 64)
    for r in report["results"]:
        mark = "✅" if r["passed"] else "❌"
        print(f"{mark} {r['id']}  (정답: {r['truth']})")
        if not r["passed"]:
            for f in r["fails"]:
                print(f"     - {f}")
            print(f"     답변: {r['text'][:160].replace(chr(10), ' ')}")
    sys.exit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
