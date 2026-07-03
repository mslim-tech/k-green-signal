# tests/test_gate_adjudicate.py
# -----------------------------------------------------------------------------
# 하이브리드 게이트의 결정적 단위 검증 (LLM·과금 없음).
#   - is_uncertain_high: 인덱싱을 막는 '불확실 high' 판정(게이트·사이드바·adjudicate 단일 소스)
#   - _apply_verdict: LLM 검증 반영 — uncertain/값없음은 절대 쓰지 않는다(추측은 데이터가 아니다)
#   - adjudicate_row: RAG_FAKE_LLM 이면 항상 uncertain(데이터 불변)
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.curate import adjudicate, corrections
from rag.curate.validate import is_uncertain_high


@pytest.mark.parametrize("row,expected", [
    # 고신뢰 + 숫자 + 합계정상 → 완화(분석 플래그만 있는 충실 추출 = 인덱싱 허용)
    ({"review_priority": "high", "extraction_confidence": "high",
      "value": "55.1", "flag_sum_violation": "False"}, False),
    # 빈 값 → 차단
    ({"review_priority": "high", "extraction_confidence": "high",
      "value": "", "flag_sum_violation": "False"}, True),
    # 저신뢰 → 차단
    ({"review_priority": "high", "extraction_confidence": "low",
      "value": "55.1", "flag_sum_violation": "False"}, True),
    # 합계 이상 → 차단 (CSV 문자열 'True' 직렬화 계약을 고정)
    ({"review_priority": "high", "extraction_confidence": "high",
      "value": "55.1", "flag_sum_violation": "True"}, True),
    # high 가 아니면 대상 아님
    ({"review_priority": "medium", "extraction_confidence": "low", "value": ""}, False),
])
def test_is_uncertain_high(row, expected):
    row = {"year": "2025", "std_id": "X", "std_response_label": "L", **row}
    assert is_uncertain_high(row, reviewed=set()) is expected


def test_is_uncertain_high_reviewed_excluded():
    row = {"year": "2025", "std_id": "X", "std_response_label": "L",
           "review_priority": "high", "extraction_confidence": "low", "value": ""}
    assert is_uncertain_high(row, reviewed={corrections.row_key(row)}) is False


ROW = {"year": "2025", "std_id": "X", "std_response_label": "L", "value": "10.0"}


def test_apply_verdict_agree_and_correct(monkeypatch):
    calls = []
    monkeypatch.setattr(adjudicate.corrections, "add_correction",
                        lambda row, **kw: calls.append(kw) or {})
    monkeypatch.setattr(adjudicate.corrections, "reviewed_keys", lambda records=None: set())
    v = adjudicate._apply_verdict(dict(ROW), {"verdict": "agree", "value": None, "reason": "r"})
    assert v == "confirmed"
    assert calls[-1]["status"] == corrections.STATUS_LLM_VERIFIED
    assert calls[-1]["new_value"] == "10.0"            # 원문 지지 → 추출값 그대로 확정
    v = adjudicate._apply_verdict(dict(ROW), {"verdict": "correct", "value": 12.5, "reason": "r"})
    assert v == "corrected"
    assert calls[-1]["new_value"] == "12.5"            # 원문값으로 교정


def test_apply_verdict_escalates_without_writing(monkeypatch):
    # uncertain·값 없는 correct 는 아무것도 쓰지 않는다 — "추측은 데이터가 아니다".
    def _boom(*a, **kw):
        raise AssertionError("escalated 인데 corrections 에 기록했다")
    monkeypatch.setattr(adjudicate.corrections, "add_correction", _boom)
    monkeypatch.setattr(adjudicate.corrections, "reviewed_keys", lambda records=None: set())
    assert adjudicate._apply_verdict(dict(ROW), {"verdict": "uncertain", "value": None, "reason": ""}) == "escalated"
    assert adjudicate._apply_verdict(dict(ROW), {"verdict": "correct", "value": None, "reason": ""}) == "escalated"


def test_apply_verdict_agree_on_blank_value_escalates(monkeypatch):
    # 빈 값 행에 'agree'는 결측을 확정하는 셈 — 쓰지 않고 사람에게 남긴다.
    def _boom(*a, **kw):
        raise AssertionError("빈 값 agree 인데 corrections 에 기록했다")
    monkeypatch.setattr(adjudicate.corrections, "add_correction", _boom)
    monkeypatch.setattr(adjudicate.corrections, "reviewed_keys", lambda records=None: set())
    blank = {**ROW, "value": ""}
    assert adjudicate._apply_verdict(blank, {"verdict": "agree", "value": None, "reason": ""}) == "escalated"


def test_apply_verdict_respects_human_review(monkeypatch):
    # LLM 판독(수 초) 사이 사람이 같은 행을 검수했으면 덮어쓰지 않는다(사람 판단 우선).
    monkeypatch.setattr(adjudicate.corrections, "reviewed_keys",
                        lambda records=None: {corrections.row_key(ROW)})
    def _boom(*a, **kw):
        raise AssertionError("사람 검수를 LLM 이 덮어썼다")
    monkeypatch.setattr(adjudicate.corrections, "add_correction", _boom)
    assert adjudicate._apply_verdict(dict(ROW), {"verdict": "agree", "value": None, "reason": ""}) == "skipped"
    assert adjudicate._apply_verdict(dict(ROW), {"verdict": "correct", "value": 12.5, "reason": ""}) == "skipped"


def test_adjudicate_row_fake_mode_is_uncertain(monkeypatch):
    monkeypatch.setenv("RAG_FAKE_LLM", "1")
    assert adjudicate.adjudicate_row(None, dict(ROW))["verdict"] == "uncertain"
