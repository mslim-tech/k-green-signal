# rag/curate/external_context.py
# -----------------------------------------------------------------------------
# 큐레이션된 '외부 맥락'(그해 뉴스·사회적 사건)의 단일 로더.
#
# 이 파일의 역할:
#   - curation/external_context.json(사람 확정 사건 목록)을 읽어 한 곳에서 공급한다.
#     (a) 청킹(chunking.py)이 parser_type='external_context' 지식청크로 인덱싱 → RAG(특히
#         '데이터 기반 제언')가 데이터 변화를 '그해 사건'과 대조해 상황 적응형 해석을 만든다.
#     (b) 앱(ui/signal.py)의 '변곡점 × 외부 맥락' 패널도 이 파일을 읽는다(단일 소스).
#
# 원칙: 이 사건은 정형 설문값(데이터)이 아니라 '데이터를 어떤 맥락에서 볼지'에 대한 사람 확정
#       지식이다. parser_type 으로 데이터와 명확히 구분해 인덱싱하고, 상관·맥락일 뿐 인과를
#       단정하지 않는다(프롬프트가 강제).
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
from pathlib import Path

EVENTS_PATH = Path(__file__).resolve().parents[2] / "curation" / "external_context.json"


def load_events() -> list[dict]:
    """ 외부 맥락 사건 목록. 파일 없으면 빈 목록(파이프라인은 계속 동작). """
    if not EVENTS_PATH.exists():
        return []
    data = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    return data.get("entries", [])
