# rag/curate/implications.py
# -----------------------------------------------------------------------------
# 큐레이션된 '보고서 시사점'(요약·시사점 절의 정성적 결론)의 단일 로더.
#
# 이 파일의 역할:
#   - curation/implications.json(사람 확정 시사점 목록)을 읽어 한 곳에서 공급한다.
#     청킹(chunking.py)이 parser_type='implication' 지식청크로 인덱싱 → RAG(특히
#     '데이터 기반 제언')가 정량 수치 나열을 넘어 '당시 연구원의 정책적 결론'을 출처와
#     함께 인용하도록 한다.
#
# 원칙: 이 시사점은 정형 설문값(데이터)이 아니라, 각 연도 결과보고서 요약본에 실제로 있는
#       '해석·결론' 지식이다. parser_type 으로 데이터와 명확히 구분해 인덱싱하고, 반드시
#       원문 근거(연도·페이지)와 함께 사람이 확정해 넣는다(추측·창작 금지 — 빈 목록도 정상).
#   (external_context.py·methodology.py 와 같은 단일 로더 패턴.)
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
from pathlib import Path

IMPLICATIONS_PATH = Path(__file__).resolve().parents[2] / "curation" / "implications.json"


def load_implications() -> list[dict]:
    """ 보고서 시사점 목록. 파일 없거나 비어 있으면 빈 목록(파이프라인은 계속 동작). """
    if not IMPLICATIONS_PATH.exists():
        return []
    data = json.loads(IMPLICATIONS_PATH.read_text(encoding="utf-8"))
    return data.get("entries", [])
