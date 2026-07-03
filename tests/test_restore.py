# tests/test_restore.py
# -----------------------------------------------------------------------------
# 결정적(LLM 없는) 단위 검증: 사람이 확정한 값(corrections)이 지어냄 없이
# 인덱스용 청크까지 '복원'되는지.
#
#   왜 결정적인가: corrections.jsonl + chunking 은 순수 데이터 변환이라 LLM 변동이
#   없다. 사용자의 검수 노력이 인덱스에 반영되는지를 회귀로 단단히 지킨다.
#
#   무엇을 지키나(특정 표에 묶지 않는다): confirmed_only_rows 가 '소스에 대응 행이
#   없는' 확정값만 복원하고 — (a) 표 통째 누락, (b) 기존 표의 새 응답라벨 — 그 값이
#   빈칸을 지어내지 않고(‘값 있는 행만’) 청크 본문까지 도달하는지.
#   과거엔 2023 표 3-60('친환경제품 확대 희망')이 대표 사례였으나, 추출 개선으로
#   그 표는 이제 소스에서 직접 나온다(page 74). 그래서 테스트는 특정 표가 아니라
#   '그 시점에 실제로 복원되는 확정값 전부'의 계약을 지킨다. 복원할 확정값이 없으면
#   (모두 소스에 흡수됨) 지킬 대상이 없으므로 실패가 아니라 skip 한다.
# -----------------------------------------------------------------------------

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

CORRECTIONS = PROJECT_ROOT / "outputs" / "corrections.jsonl"


@pytest.mark.skipif(not CORRECTIONS.exists(),
                    reason="outputs/corrections.jsonl 없음 — 로컬 검수 데이터 필요")
def test_confirmed_corrections_reach_index():
    from rag.curate import corrections
    from rag.retrieval import chunking

    # 소스 CSV 로드(있으면). confirmed_only_rows 는 '이 소스에 대응 행이 없는' 확정값만 복원한다.
    src_rows = []
    if chunking.SOURCE_CSV.exists():
        with open(chunking.SOURCE_CSV, encoding="utf-8-sig", newline="") as f:
            src_rows = list(csv.DictReader(f))

    restored = corrections.confirmed_only_rows(src_rows)
    if not restored:
        pytest.skip("복원 대상 확정값이 없음 — 모든 corrections 가 소스에 흡수됨(지킬 대상 없음)")

    # 1) 지어내지 않는다: 복원 행은 모두 값이 있어야 한다(빈 값 금지).
    assert all((r.get("value") or "").strip() for r in restored), "빈 값 행이 복원에 섞임"

    # 2) 복원된 (year, std_id) 는 인덱스용 청크로 만들어져야 한다(검수 노력이 인덱스에 도달).
    chunks = chunking.build_chunks(chunking.load_rows())
    by_id = {c["id"]: c for c in chunks}
    for r in restored:
        cid = f"{r['year']}__{r['std_id']}"
        assert cid in by_id, f"복원된 표가 청크에 없음: {cid}"

    # 3) 복원 값이 그 청크 본문에 실제 표기(‘라벨: 값%’)로 보여야 한다(검색·인용 대상).
    for r in restored:
        cid = f"{r['year']}__{r['std_id']}"
        needle = f"{r['std_response_label']}: {r['value']}%"
        assert needle in by_id[cid]["text"], f"청크 본문에 복원값 없음: {cid} / {needle!r}"
