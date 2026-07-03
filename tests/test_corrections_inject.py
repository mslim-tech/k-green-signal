# tests/test_corrections_inject.py
# -----------------------------------------------------------------------------
# 결정적(LLM 없는) 단위 검증: corrections.confirmed_only_rows 의 'inject' 경로.
#
# 배경: 비전 재판독(refill_vision)이 기존 표의 '새 응답라벨' 값을 vision_candidates 로
#   제안 → 검수 탭에서 사람이 확정 → corrections.jsonl(fixed). 이때 그 (year, std_id,
#   std_response_label) 행이 소스에 없으면 apply_corrections 로는 못 들어간다.
#   confirmed_only_rows 가 '전체 키 미존재'를 기준으로 이 확정값을 인덱싱용 행으로
#   복원해야 한다. (임시 corrections.jsonl 만 쓰므로 실데이터·과금 없이 결정적.)
# -----------------------------------------------------------------------------

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.curate import corrections


def _write(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_inject_new_label_under_existing_std_id(tmp_path):
    """ 같은 std_id 는 있지만 그 응답라벨 행은 없을 때(inject), 확정값이 복원되고
        메타(std_label/source/page/unit)를 형제 행에서 상속해야 한다. """
    cpath = tmp_path / "corrections.jsonl"
    existing = [{
        "year": "2025", "std_id": "환경표지_속성별신뢰도",
        "std_response_label": "", "value": "",           # 빈 라벨 형제(메타 상속원)
        "std_label": "환경표지 제도/제품 속성별 신뢰도",
        "source": "2025보고서.pdf", "unit": "%", "page_start": "39",
        "question_summary": "속성별 신뢰 정도",
    }]
    _write(cpath, [{
        "year": "2025", "std_id": "환경표지_속성별신뢰도",
        "std_response_label": "건강, 안전 도움", "field": "value",
        "old_value": "", "new_value": "95", "status": "fixed",
        "note": "비전 재판독 확정 — 2025보고서.pdf p.39",
        "reviewer": "", "ts": "2026-07-02T00:00:00",
    }])

    rows = corrections.confirmed_only_rows(existing, path=cpath)
    got = {r["std_response_label"]: r for r in rows}
    assert "건강, 안전 도움" in got, "inject 신규 라벨이 복원되지 않음"
    r = got["건강, 안전 도움"]
    assert r["value"] == "95"
    assert r["std_id"] == "환경표지_속성별신뢰도"
    assert r["std_label"] == "환경표지 제도/제품 속성별 신뢰도"   # 형제에서 상속
    assert r["source"] == "2025보고서.pdf"
    assert r["page_start"] == "39"                              # note p.39 / 형제
    assert r["unit"] == "%"


def test_existing_fullkey_not_injected(tmp_path):
    """ 전체 키가 이미 있으면 복원하지 않는다(값 채움은 apply_corrections 담당 → 중복 방지). """
    cpath = tmp_path / "corrections.jsonl"
    existing = [{"year": "2025", "std_id": "X", "std_response_label": "L", "value": "1"}]
    _write(cpath, [{
        "year": "2025", "std_id": "X", "std_response_label": "L", "field": "value",
        "old_value": "1", "new_value": "2", "status": "fixed", "note": "", "ts": "t",
    }])
    rows = corrections.confirmed_only_rows(existing, path=cpath)
    assert not any(r["std_id"] == "X" for r in rows), "이미 있는 행이 중복 복원됨"


def test_inject_page_blank_when_group_spans_multiple_pages(tmp_path):
    """ Bug 5: note 에 p.NN 이 없고 (year,std_id) 그룹이 여러 페이지에 걸쳐 있으면,
        새 라벨의 위치가 불확실하므로 형제 페이지를 '지어내지' 않고 빈칸으로 둔다. """
    cpath = tmp_path / "corrections.jsonl"
    existing = [
        {"year": "2025", "std_id": "Q", "std_response_label": "가", "value": "10",
         "std_label": "문항 Q", "source": "r.pdf", "unit": "%", "page_start": "39"},
        {"year": "2025", "std_id": "Q", "std_response_label": "나", "value": "20",
         "std_label": "문항 Q", "source": "r.pdf", "unit": "%", "page_start": "42"},
    ]
    _write(cpath, [{
        "year": "2025", "std_id": "Q", "std_response_label": "다", "field": "value",
        "old_value": "", "new_value": "30", "status": "fixed",
        "note": "그림 3-2 확인",   # p.NN 없음
        "ts": "t",
    }])
    rows = corrections.confirmed_only_rows(existing, path=cpath)
    r = next(r for r in rows if r["std_response_label"] == "다")
    assert r["page_start"] == "", "여러 페이지 그룹인데 형제 페이지를 지어냄"
    assert r["page_end"] == ""


def test_inject_page_inherited_when_group_single_page(tmp_path):
    """ Bug 5: 그룹이 단일 페이지면 note 에 p.NN 이 없어도 그 페이지를 상속한다(표=한 페이지). """
    cpath = tmp_path / "corrections.jsonl"
    existing = [
        {"year": "2025", "std_id": "Q", "std_response_label": "가", "value": "10",
         "std_label": "문항 Q", "source": "r.pdf", "unit": "%", "page_start": "39"},
        {"year": "2025", "std_id": "Q", "std_response_label": "나", "value": "20",
         "std_label": "문항 Q", "source": "r.pdf", "unit": "%", "page_start": "39"},
    ]
    _write(cpath, [{
        "year": "2025", "std_id": "Q", "std_response_label": "다", "field": "value",
        "old_value": "", "new_value": "30", "status": "fixed", "note": "확인", "ts": "t",
    }])
    rows = corrections.confirmed_only_rows(existing, path=cpath)
    r = next(r for r in rows if r["std_response_label"] == "다")
    assert r["page_start"] == "39", "단일 페이지 그룹인데 페이지를 상속하지 않음"


def test_blank_and_skip_excluded(tmp_path):
    """ 빈 값/skip 은 복원에서 제외(지어내지 않는다). """
    cpath = tmp_path / "corrections.jsonl"
    _write(cpath, [
        {"year": "2025", "std_id": "Y", "std_response_label": "a", "field": "value",
         "old_value": "", "new_value": "", "status": "fixed", "note": "", "ts": "t"},
        {"year": "2025", "std_id": "Y", "std_response_label": "b", "field": "value",
         "old_value": "", "new_value": "", "status": "skip", "note": "", "ts": "t"},
    ])
    rows = corrections.confirmed_only_rows([], path=cpath)
    assert not rows, "빈 값/skip 이 복원에 섞임"


def test_llm_verified_applies_like_fixed(tmp_path):
    """ 신규 status='llm_verified'(LLM 검증 확정)는 'fixed'처럼 값이 반영되고,
        소스에 그 행이 없으면 복원(inject)도 되어야 한다 — 하이브리드 게이트 회귀 가드. """
    cpath = tmp_path / "corrections.jsonl"
    _write(cpath, [{
        "year": "2025", "std_id": "X", "std_response_label": "L", "field": "value",
        "old_value": "1", "new_value": "2", "status": "llm_verified",
        "note": "원문 대조 일치", "reviewer": "llm:adjudicate", "ts": "2026-07-03T00:00:00",
    }])
    rows, n = corrections.apply_corrections(
        [{"year": "2025", "std_id": "X", "std_response_label": "L", "value": "1"}], path=cpath)
    assert n == 1 and rows[0]["value"] == "2"          # 오버레이(값 교체)
    injected = corrections.confirmed_only_rows([], path=cpath)
    assert injected and injected[0]["value"] == "2"    # 소스에 없으면 복원(inject)
