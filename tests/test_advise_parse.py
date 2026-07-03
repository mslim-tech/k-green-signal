# tests/test_advise_parse.py
# -----------------------------------------------------------------------------
# advise 답변 구조 파서(parse_advise_sections)의 결정적 단위 검증 (LLM 불필요).
#   - 헤딩 계약을 지킨 텍스트 → 4갈래 + 근거 사실로 분해된다.
#   - 헤딩이 없는(계약을 어긴) 텍스트 → None(화면은 원문 폴백).
#   - 갈래가 1개뿐이면 → None(어설픈 부분 구조를 강요하지 않는다).
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.retrieval.answer import parse_advise_sections


CONTRACT_TEXT = (
    "종합하면 인지도 지표는 상승 추세다.\n"
    "### 💡 제언(추론)\n"
    "#### KEEP(유지)\n- 환경표지 인지도 유지. [출처: a.pdf p.1]\n"
    "#### ADD(신설)\n- 그린워싱 인식 신설. [출처: a.pdf p.2]\n"
    "#### DROP/축소\n- 데이터로 판단 불가\n"
    "#### FIX(설계 교정)\n- 척도 표준화. [출처: 방법론 주석(큐레이션)]\n"
    "### 📊 근거 사실\n- 인지율 85.2%. [출처: a.pdf p.1]\n"
)


def test_parse_contract_text_into_sections():
    s = parse_advise_sections(CONTRACT_TEXT)
    assert s is not None
    assert [kind for kind, _, _ in s.advice] == ["KEEP", "ADD", "DROP", "FIX"]
    assert "환경표지 인지도 유지" in s.advice[0][2]
    assert "데이터로 판단 불가" in s.advice[2][2]
    assert "인지율 85.2%" in s.facts
    assert "상승 추세" in s.preamble          # 첫 헤딩 앞 서문 보존


def test_parse_returns_none_without_headings():
    # 계약을 어긴 자유 서술 → 구조를 합성하지 않고 None(원문 폴백).
    assert parse_advise_sections("KEEP 하세요. ADD 하세요. 근거는 없습니다.") is None
    assert parse_advise_sections("") is None


def test_parse_returns_none_with_single_branch():
    text = "### 💡 제언(추론)\n#### KEEP(유지)\n- 유지.\n"
    assert parse_advise_sections(text) is None
