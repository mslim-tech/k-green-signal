# rag/curate/methodology.py
# -----------------------------------------------------------------------------
# 큐레이션된 '방법론 주석'(비교 유의 지식)의 단일 로더.
#
# 이 파일의 역할:
#   - curation/methodology_notes.json(사람 확정 지식)을 읽어 한 곳에서 공급한다.
#     (a) 청킹(chunking.py)이 parser_type='methodology' 지식청크로 인덱싱 → RAG 가
#         '척도 변경 아티팩트를 실제 추세로 오독'하지 않게 근거로 쓴다.
#     (b) 앱(app.py)의 지표카드 '비교 유의' 캡션도 이 파일을 읽는다(드리프트 방지).
#
# 원칙: 이 주석은 정형 설문값(데이터)이 아니라 '데이터를 어떻게 비교/해석할지'에 대한
#       사람 확정 지식이다. parser_type 으로 데이터와 명확히 구분해 인덱싱한다.
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
from pathlib import Path

NOTES_PATH = Path(__file__).resolve().parents[2] / "curation" / "methodology_notes.json"


def load_notes() -> list[dict]:
    """ 방법론 주석 엔트리 목록. 파일 없으면 빈 목록(파이프라인은 계속 동작). """
    if not NOTES_PATH.exists():
        return []
    data = json.loads(NOTES_PATH.read_text(encoding="utf-8"))
    return data.get("entries", [])


def caveats_by_std_id() -> dict[str, str]:
    """ 앱용: std_id → 비교유의 캡션(note). 앱 INDICATOR_CAVEATS 를 대체하는 단일 소스. """
    return {n["std_id"]: n["note"] for n in load_notes()
            if n.get("std_id") and n.get("note")}
