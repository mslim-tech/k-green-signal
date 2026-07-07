# tests/test_external_search.py
# -----------------------------------------------------------------------------
# 외부 맥락 후보 '검색 스텁'의 결정적 단위 검증 (LLM·과금·네트워크 불필요).
#   - build_search_queries: 키워드+연도로 검색어 후보 생성(중복 제거·개수 상한)
#   - search_external_context: 큐레이션 사건 기반 스텁이 연도창·키워드로 후보를 뽑고
#     유형(정책/제도·사회이슈·언론보도)을 분류하는지
#   - group_by_category: 유형 순서 유지·빈 유형 제거
# 검증: uv run python -m pytest tests/test_external_search.py -q
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.curate import external_search as ext


def test_build_queries_starts_with_bare_keyword_year():
    qs = ext.build_search_queries("그린카드", 2022)
    assert qs[0] == "그린카드 2022"
    assert all("2022" in q for q in qs)
    assert len(qs) == len(set(qs))          # 중복 없음
    assert len(qs) <= 6                      # 개수 상한


def test_build_queries_uses_indicator_when_keyword_empty():
    qs = ext.build_search_queries("", 2024, indicator_label="녹색제품 인지도")
    assert qs and qs[0].startswith("녹색제품 인지도")


def test_build_queries_empty_when_no_terms():
    assert ext.build_search_queries("", 2024) == []


def test_search_stub_matches_year_window_and_keyword():
    # 2023 그린워싱 사건(match 에 '그린워싱','신뢰','환경표시' 포함, 출처 한국경제)만 잡혀야 한다.
    hits = ext.search_external_context(
        "그린워싱", 2023, haystack="그린워싱 신뢰 환경표시 부당")
    assert hits, "2023 그린워싱 사건이 스텁 결과에 있어야 한다"
    assert all(abs(h.year - 2023) <= 1 for h in hits)   # 연도창 ±1
    assert all(h.is_stub for h in hits)                 # 스텁 표식
    greenwash = [h for h in hits if "그린워싱" in h.summary]
    assert greenwash, "그린워싱 후보가 포함되어야 한다"
    assert greenwash[0].category == ext.CATEGORY_PRESS  # 한국경제 → 언론 보도
    assert greenwash[0].url.startswith("http")          # 출처 링크 존재


def test_search_stub_no_keyword_match_returns_empty():
    # 큐레이션 어느 태그와도 겹치지 않는 키워드 → 후보 없음(억지 매칭 안 함).
    hits = ext.search_external_context("존재하지않는키워드zzz", 2023,
                                       haystack="존재하지않는키워드zzz")
    assert hits == []


def test_classify_policy_vs_social():
    assert ext._classify({"source": "대한민국 정책브리핑",
                          "title": "2050 탄소중립 선언"}) == ext.CATEGORY_POLICY
    assert ext._classify({"source": "위키백과",
                          "title": "가습기살균제 사건"}) == ext.CATEGORY_SOCIAL
    assert ext._classify({"source": "세계일보",
                          "title": "텀블러 사용 급증"}) == ext.CATEGORY_PRESS


def test_group_by_category_orders_and_drops_empty():
    hits = [
        ext.ExternalHit(ext.CATEGORY_PRESS, 2023, "p", "p", "s", "u"),
        ext.ExternalHit(ext.CATEGORY_POLICY, 2023, "a", "a", "s", "u"),
    ]
    groups = ext.group_by_category(hits)
    assert list(groups.keys()) == [ext.CATEGORY_POLICY, ext.CATEGORY_PRESS]  # 순서 + 빈 유형 제거


# ── 실 웹검색 provider의 순수 부분(실 API 호출 없이 검증) ──────────────────────

def test_dispatch_uses_stub_by_default():
    # use_web 기본 False → 스텁 경로(모든 항목 is_stub=True), 실 API 미호출.
    hits = ext.search_external_context("그린워싱", 2023, haystack="그린워싱 신뢰 환경표시")
    assert hits and all(h.is_stub for h in hits)


def test_parse_candidates_plain_and_fenced_and_garbage():
    plain = '{"candidates": [{"category": "정책/제도", "year": 2025, "title": "t", ' \
            '"summary": "s", "source": "src", "url": "http://x"}]}'
    assert ext._parse_candidates(plain)[0]["title"] == "t"
    fenced = "설명...\n```json\n" + plain + "\n```\n꼬리말"
    assert len(ext._parse_candidates(fenced)) == 1        # 코드펜스·앞뒤 잡음에 관대
    assert ext._parse_candidates("전혀 JSON 아님") == []    # 실패 시 빈 목록(→ 스텁 폴백)


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ext, "_CACHE_PATH", tmp_path / "external_search_cache.json")
    assert ext._cache_get("탄소", 2021) is None            # 최초엔 미스
    hits = [ext.ExternalHit(ext.CATEGORY_POLICY, 2021, "탄소중립기본법", "요약",
                            "정책브리핑", "http://x", query="탄소 2021", is_stub=False)]
    ext._cache_put("탄소", 2021, hits)
    got = ext._cache_get("탄소", 2021)                      # 히트 → ExternalHit 복원
    assert got and got[0].title == "탄소중립기본법" and got[0].is_stub is False
    assert ext._cache_get("탄소", 2020) is None            # 키(연도) 다르면 미스
