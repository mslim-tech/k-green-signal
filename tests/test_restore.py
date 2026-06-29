# tests/test_restore.py
# -----------------------------------------------------------------------------
# 결정적(LLM 없는) 단위 검증: 추출이 깨져 드롭됐지만 사람이 확정한 표(2023 표 3-60
# '친환경제품 확대 희망')가 corrections → chunking 으로 인덱스용 청크에 복원되는지.
#
#   왜 결정적인가: corrections.jsonl + chunking 은 순수 데이터 변환이라 LLM 변동이
#   없다. 사용자의 검수 노력이 인덱스에 반영되는지를 회귀로 단단히 지킨다.
#   (실제 검색/답변의 표 구분은 LLM 변동이 있어 별도 — eval 은 안정적 질문만 게이트.)
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

CORRECTIONS = PROJECT_ROOT / "outputs" / "corrections.jsonl"


@pytest.mark.skipif(not CORRECTIONS.exists(),
                    reason="outputs/corrections.jsonl 없음 — 로컬 검수 데이터 필요")
def test_table_3_60_restored_from_corrections():
    from rag import corrections, chunking

    # 1) 소스 CSV 에는 없어야 한다(추출 깨져 드롭된 표). 복원은 (year, std_id) 단위라
    #    2023 표 3-60 의 키만 부재하면 된다(옛 연도 통합으로 같은 std_id 의 2016/2017
    #    '확대희망' 행은 소스에 있지만 2023 표 3-60 은 여전히 corrections 에만 있음).
    src_rows = []
    if chunking.SOURCE_CSV.exists():
        import csv
        with open(chunking.SOURCE_CSV, encoding="utf-8-sig", newline="") as f:
            src_rows = list(csv.DictReader(f))
    assert not any(r.get("std_id") == "친환경제품_확대희망품목" and r.get("year") == "2023"
                   for r in src_rows), \
        "2023 표 3-60 이 소스 CSV 에 이미 있음 — 이 테스트 전제(누락)와 다름"

    # 2) confirmed_only_rows 가 사람 확정값으로 그 표를 복원해야 한다.
    restored = corrections.confirmed_only_rows(src_rows)
    t360 = [r for r in restored if r.get("std_id") == "친환경제품_확대희망품목"]
    assert t360, "표 3-60 이 corrections 에서 복원되지 않음"

    # 보일러 6.1% 가 확정값으로 들어와야 한다(사람 확정 핵심값).
    by_label = {r["std_response_label"]: r["value"] for r in t360}
    assert by_label.get("친환경적인 보일러") == "6.1", \
        f"보일러 확정값(6.1)이 복원 안 됨: {by_label.get('친환경적인 보일러')!r}"

    # 값 없는(빈) 라벨은 복원에서 제외돼야 한다(지어내지 않음).
    assert all((r.get("value") or "").strip() for r in t360), "빈 값 행이 복원에 섞임"

    # 3) chunking 이 그 표를 (year, std_id) 청크로 만들어야 한다.
    chunks = chunking.build_chunks(chunking.load_rows())
    ids = {c["id"] for c in chunks}
    assert "2023__친환경제품_확대희망품목" in ids, "복원된 표가 청크에 없음"

    # 청크 본문에 보일러 6.1% 가 보여야 한다(검색·인용 대상).
    chunk = next(c for c in chunks if c["id"] == "2023__친환경제품_확대희망품목")
    assert "친환경적인 보일러: 6.1%" in chunk["text"], "청크 본문에 보일러 6.1% 없음"
