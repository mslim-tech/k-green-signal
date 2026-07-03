# tests/test_bugfixes.py
# -----------------------------------------------------------------------------
# 결정적(LLM 없는) 회귀 테스트: 정합성 리뷰에서 찾아 고친 결함들을 다시 재발하지
# 않도록 잠근다. 실데이터·과금 없이 순수 함수만 검증한다.
#   - Bug 1: extract_vision_oldtable.to_records — 비전 판독값을 high 로 찍어 검수 우회
#   - Bug 3: answer._detect_year — '2000명'·'1900원' 등 수량을 연도로 오인
#   - Bug 4: chunking.build_chunks — std_id 가 None 이면 Chroma 메타 거부로 빌드 실패
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.retrieval import answer, chunking
from rag.ingest import extract_vision_oldtable


# --- Bug 1: 다년 표 비전 판독값은 high 가 아니라 검수 대상(medium + warning) ---------
def test_to_records_marks_vision_for_review():
    result = {
        "question_summary": "친환경제품 구매경험",
        "unit": "%", "multi_response": False,
        "years": [{"year": 2019, "base_n": 1000,
                   "items": [{"label": "있음", "value": 55.6}]}],
    }
    recs = extract_vision_oldtable.to_records(result, "old.pdf", 42, "구매경험")
    assert len(recs) == 1
    r = recs[0]
    # 비전 다년 판독은 '점선'(검토 후보) — high 로 실선에 새면 안 된다.
    assert r["extraction_confidence"] == "medium"
    assert (r["warning"] or "").strip()   # 경고가 있어야 review 큐가 잡는다


# --- Bug 3: 연도 감지가 수량/화폐 숫자를 연도로 오인하면 안 된다 ---------------------
def test_detect_year_ignores_quantities():
    assert answer._detect_year("2023년에 확대되길 바라는 친환경제품 1위는?") == "2023"
    assert answer._detect_year("2024년 친환경 인지율?") == "2024"
    # 오인 방지: 표본수/가격/가구수는 연도가 아니다
    assert answer._detect_year("샘플 2000명 조사에서 1위 품목은?") is None
    assert answer._detect_year("1900원대 제품 인지도는?") is None
    assert answer._detect_year("2022가구 대상 조사") is None
    # 두 연도(비교 질문)면 필터하지 않는다
    assert answer._detect_year("2023 대비 2024 변화는?") is None


# --- Bug 4: std_id 가 비어도(None) 인덱스 빌드가 깨지지 않고 문자열로 강제된다 --------
def test_build_chunks_coerces_none_std_id():
    rows = [{
        "year": "2025", "std_id": None, "std_label": "미매핑 문항",
        "std_response_label": "있음", "response_label": "있음", "value": "55.6",
        "source": "x.pdf", "page_start": "10", "page_end": "10",
        "question_summary": "질문", "warning": "",
    }]
    chunks = chunking.build_chunks(rows)
    assert len(chunks) == 1
    meta = chunks[0]["metadata"]
    # Chroma 는 None 메타를 거부 → 문자열이어야 한다
    assert meta["std_id"] == ""
    assert isinstance(meta["std_id"], str)
    assert "None" not in meta["chunk_id"]
