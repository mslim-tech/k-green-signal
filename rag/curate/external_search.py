# rag/curate/external_search.py
# -----------------------------------------------------------------------------
# 데이터 변화 지점(연도) → '외부 맥락 후보' 검색.
#
# 이 파일의 역할:
#   - 선택한 키워드 + 변화 연도로 외부 '검색어 후보'를 만든다(build_search_queries).
#   - 그 검색어로 외부 맥락 후보를 유형별(정책/제도·사회이슈·언론보도)로 돌려준다
#     (search_external_context). 두 가지 provider:
#       (a) 스텁(기본·과금 0): 사람이 확정해 둔 curation/external_context.json 을 재료로
#           결정적 결과 생성. 무키·FAKE·E2E·오류 폴백에 쓴다.
#       (b) 실 웹검색(use_web=True): OpenAI Responses API 의 web_search 툴로 실제 웹을
#           검색해 유형화한다(과금). (키워드,연도)→결과를 디스크 캐시해 재과금을 막는다.
#           호출 실패/무키면 조용히 스텁으로 폴백한다(UI 는 절대 안 깨진다).
#
# 원칙(두 흐름의 '점선' = 추측 격리): 이 결과는 확정 '데이터'가 아니라 '참고할 만한
#   외부 맥락 후보'다. 실검색 결과조차 정형 CSV/인덱스로 흘려보내지 않고 화면 표시 전용이며,
#   인과를 단정하지 않는다(그해 이런 맥락이 있었다는 참고일 뿐). 확정이 필요하면 사람이
#   corrections/curation 경로로 올린다(설계결정 #1, 아직 미구현).
#
# ⚠️ 모델 지원: 실 웹검색은 config.WEB_SEARCH_MODEL 이 web_search 툴을 지원해야 한다.
#   미지원이면 config 의 그 상수만 검색 지원 모델로 바꾼다(코드 변경 불필요).
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

# 직접 실행(python rag/curate/external_search.py)과 패키지 import 를 모두 지원(프로젝트 관례).
try:
    from rag.curate.external_context import load_events
    from rag.core.paths import OUTPUT_DIR
    from rag.core.config import WEB_SEARCH_MODEL
except ImportError:  # 스크립트로 직접 실행할 때
    from external_context import load_events
    from core.paths import OUTPUT_DIR
    from core.config import WEB_SEARCH_MODEL


# 외부 맥락 후보의 '유형' — 단순 기사 목록이 아니라 문서 유형별로 정리해 보여주기 위함.
CATEGORY_POLICY = "정책/제도"
CATEGORY_SOCIAL = "사회 이슈"
CATEGORY_PRESS = "언론 보도"
CATEGORY_ORDER = [CATEGORY_POLICY, CATEGORY_SOCIAL, CATEGORY_PRESS]


@dataclass
class ExternalHit:
    """ 외부 맥락 후보 한 건 — 확정 데이터가 아니라 '참고 후보'다.
        화면의 세 영역과 1:1: title(관련 외부 맥락 후보) · summary(요약) · source/url(출처 링크). """
    category: str          # 유형(CATEGORY_*)
    year: int              # 그 맥락이 있었던 해
    title: str             # 짧은 제목(관련 외부 맥락 후보 헤드라인)
    summary: str           # 한 줄 요약
    source: str            # 출처(매체/기관)
    url: str               # 출처 링크
    query: str = ""        # 이 후보를 부른 검색어(투명성 — 어떤 검색으로 나왔는지)
    is_stub: bool = True    # 스텁 결과 표식(실호출을 붙이면 False 로)


def build_search_queries(keyword: str, year: int,
                         indicator_label: str = "", series_label: str = "") -> list[str]:
    """ 키워드 + 변화 연도로 외부 '검색어 후보'를 만든다(사람이 읽고, 실검색이 그대로 소비할 문자열).
        추정 없이 '입력한 말 + 연도 + 유형 힌트'만 조합한다. 중복 제거 후 최대 6개.
        keyword 가 비면 지표명을 기준어로 쓴다(그래도 비면 빈 목록). """
    base = (keyword or "").strip() or (indicator_label or "").strip()
    if not base:
        return []
    y = str(year)
    cands = [
        f"{base} {y}",
        f"{base} {y} 정책 제도",
        f"{base} {y} 이슈 논란",
        f"{base} {y} 언론 보도",
    ]
    ind = (indicator_label or "").strip()
    if ind and ind != base:
        cands.append(f"{ind} {y}")
    ser = (series_label or "").strip()
    if ser and ser != base:
        cands.append(f"{base} {ser} {y}")

    out: list[str] = []
    for c in cands:
        c = " ".join(c.split())          # 공백 정리
        if c and c not in out:           # 중복 제거(순서 유지)
            out.append(c)
    return out[:6]


def _classify(event: dict) -> str:
    """ 큐레이션 사건을 유형으로 분류(스텁 결과를 '유형별'로 정리해 보여주기 위함).
        출처/제목의 단서로 정책·제도 / 사회 이슈 / 언론 보도를 가른다(표시용 휴리스틱).
        실호출을 붙이면 검색 결과 기사에도 같은 규칙(또는 도메인 기반)으로 적용할 수 있다. """
    src = event.get("source") or ""
    title = event.get("title") or ""
    if "브리핑" in src or "정책" in src or any(
            k in title for k in ("법", "제도", "시행", "도입", "제정", "개정", "규제", "선언")):
        return CATEGORY_POLICY
    if "위키" in src or any(
            k in title for k in ("사건", "파동", "사태", "대란", "논란", "불매", "검출")):
        return CATEGORY_SOCIAL
    return CATEGORY_PRESS


def _headline(full: str) -> str:
    """ 큐레이션 제목(장문)에서 짧은 헤드라인을 뽑는다 — ' — '/'(' 앞의 핵심 절.
        요약(전문)과 제목(짧게)을 구분해 보여주기 위함(원문 뜻은 summary 로 보존). """
    head = (full or "").split(" — ")[0].split("(")[0].strip()
    return head or (full or "")


def search_external_context(keyword: str, year: int, *,
                            haystack: str = "", queries: list[str] | None = None,
                            year_window: int = 1,
                            use_web: bool = False) -> list[ExternalHit]:
    """ 검색어 후보로 외부 맥락 후보를 유형별로 돌려준다.

        use_web=False(기본): 스텁(과금 0·결정적) — external_context.json 기반. 아래 _stub_search.
        use_web=True: OpenAI Responses web_search 툴로 실제 검색(과금). (키워드,연도) 디스크
          캐시로 재과금을 막고, 무키/오류면 조용히 스텁으로 폴백한다(UI 안전).

        어느 경로든 반환 항목은 '참고 후보'다(정형 데이터 아님). """
    if not use_web:
        return _stub_search(keyword, year, haystack=haystack, queries=queries,
                            year_window=year_window)

    cached = _cache_get(keyword, year)
    if cached is not None:
        return cached
    try:
        hits = _web_search(keyword, year, queries)
    except Exception:
        # 키 없음·모델 미지원·네트워크·파싱 실패 등 — 화면이 죽지 않게 스텁으로 폴백.
        return _stub_search(keyword, year, haystack=haystack, queries=queries,
                            year_window=year_window)
    _cache_put(keyword, year, hits)
    return hits


def _stub_search(keyword: str, year: int, *,
                 haystack: str = "", queries: list[str] | None = None,
                 year_window: int = 1) -> list[ExternalHit]:
    """ (스텁) external_context.json 을 재료로 (대상 연도 ±year_window · 키워드 매치) 후보를
        뽑아 유형만 분류한다 — 과금 0·결정적. 실검색이 불가/불필요할 때의 기본·폴백 경로.
        매칭은 기존 관례와 동일: 각 사건의 match 태그 중 하나라도 haystack(키워드+지표명 등)에
        들어 있으면 관련으로 본다. haystack 이 비면 keyword 로 대체. 모든 항목 is_stub=True. """
    hay = (haystack or keyword or "").lower()
    hits: list[ExternalHit] = []
    q0 = (queries or [f"{keyword} {year}".strip()])[0]
    for e in load_events():
        ey = int(e.get("year", 0) or 0)
        if abs(ey - year) > year_window:                 # 대상 연도 근처만
            continue
        tags = [str(m).lower() for m in e.get("match", [])]
        if not any(t in hay for t in tags):              # 키워드·지표와 무관하면 제외
            continue
        full = e.get("title", "")
        hits.append(ExternalHit(
            category=_classify(e),
            year=ey,
            title=_headline(full),
            summary=full,                # 큐레이션엔 별도 요약이 없어 원문 전문을 요약으로 쓴다
            source=e.get("source", ""),
            url=e.get("url", ""),
            query=q0,
        ))
    # 유형 순서(정책→사회→언론) → 그 안에서 대상 연도에 가까운 순.
    hits.sort(key=lambda h: (CATEGORY_ORDER.index(h.category) if h.category in CATEGORY_ORDER else 9,
                             abs(h.year - year)))
    return hits


# ── 실 웹검색 provider (OpenAI Responses API · web_search 툴) ─────────────────
#    참고 후보만 만들며 인과를 단정하지 않게 프롬프트로 강제한다.
_WEB_PROMPT = (
    "너는 한국의 환경·소비 정책 리서처다. 아래 검색어들로 웹을 검색해, '{kw}'와 관련해 "
    "{year}년(전후 1년 포함) 대한민국에서 실제로 있었던 일을 최대 6건 찾아라.\n"
    "검색어 후보: {queries}\n\n"
    "각 항목을 유형으로 나눠라 — 반드시 다음 셋 중 하나: '정책/제도', '사회 이슈', '언론 보도'.\n"
    "실제 출처(기관/매체명)와 원문 URL을 반드시 포함하라. 추측·창작 금지(찾은 것만).\n"
    "인과를 단정하지 마라(‘이 변화의 원인’ 같은 표현 금지) — 그해 참고 맥락일 뿐이다.\n\n"
    "오직 아래 JSON 만 출력하라(설명 문장 없이):\n"
    '{{"candidates": [{{"category": "정책/제도|사회 이슈|언론 보도", "year": 2025, '
    '"title": "짧은 제목", "summary": "한 줄 요약", "source": "매체/기관", "url": "https://..."}}]}}'
)


def _web_search(keyword: str, year: int, queries: list[str] | None) -> list[ExternalHit]:
    """ Responses API 의 web_search 툴로 실제 검색 → JSON 파싱 → ExternalHit(is_stub=False).
        키가 없으면 get_client() 가 예외를 던져 상위에서 스텁으로 폴백된다. """
    try:
        from rag.ingest.extract import get_client
    except ImportError:
        from ingest.extract import get_client

    qs = ", ".join(queries or [f"{keyword} {year}"])
    prompt = _WEB_PROMPT.format(kw=keyword or "(키워드 없음)", year=year, queries=qs)
    client = get_client()
    resp = client.responses.create(
        model=WEB_SEARCH_MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
    )
    data = _parse_candidates(getattr(resp, "output_text", "") or "")
    q0 = (queries or [f"{keyword} {year}".strip()])[0]
    hits: list[ExternalHit] = []
    for c in data:
        cat = c.get("category")
        if cat not in CATEGORY_ORDER:
            cat = CATEGORY_PRESS                 # 알 수 없는 유형은 '언론 보도'로
        try:
            cy = int(c.get("year") or year)
        except (TypeError, ValueError):
            cy = year
        hits.append(ExternalHit(
            category=cat, year=cy,
            title=(c.get("title") or "").strip(),
            summary=(c.get("summary") or "").strip(),
            source=(c.get("source") or "").strip(),
            url=(c.get("url") or "").strip(),
            query=q0, is_stub=False,
        ))
    hits.sort(key=lambda h: (CATEGORY_ORDER.index(h.category) if h.category in CATEGORY_ORDER else 9,
                             abs(h.year - year)))
    return hits


def _parse_candidates(text: str) -> list[dict]:
    """ 모델 출력에서 candidates 리스트를 꺼낸다. 코드펜스/앞뒤 잡음에 관대하게 — 첫 '{' ~ 마지막
        '}' 구간을 JSON 으로 시도한다. 실패하면 빈 목록(상위에서 스텁 폴백). """
    t = (text or "").strip()
    if "```" in t:                                # ```json ... ``` 펜스 제거
        t = t.split("```")[1] if len(t.split("```")) > 1 else t
        t = t.split("\n", 1)[-1] if t.lower().startswith("json") else t
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1 or j < i:
        return []
    try:
        obj = json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return []
    cands = obj.get("candidates", []) if isinstance(obj, dict) else []
    return [c for c in cands if isinstance(c, dict)]


# ── (키워드,연도) → 결과 디스크 캐시: 같은 조회의 재과금을 막는다 ───────────────
_CACHE_PATH = OUTPUT_DIR / "external_search_cache.json"


def _cache_key(keyword: str, year: int) -> str:
    return f"{(keyword or '').strip().lower()}|{year}"


def _cache_load() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_get(keyword: str, year: int) -> list[ExternalHit] | None:
    rec = _cache_load().get(_cache_key(keyword, year))
    if rec is None:
        return None
    return [ExternalHit(**h) for h in rec]


def _cache_put(keyword: str, year: int, hits: list[ExternalHit]) -> None:
    """ 캐시에 저장(디렉터리 없으면 만든다). 쓰기 실패는 조용히 무시(캐시는 최적화일 뿐). """
    try:
        data = _cache_load()
        data[_cache_key(keyword, year)] = [asdict(h) for h in hits]
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def group_by_category(hits: list[ExternalHit]) -> dict[str, list[ExternalHit]]:
    """ 후보를 유형별로 묶는다(화면에서 유형별 섹션으로 렌더용). 순서는 CATEGORY_ORDER,
        비어 있는 유형은 뺀다. """
    out: dict[str, list[ExternalHit]] = {c: [] for c in CATEGORY_ORDER}
    for h in hits:
        out.setdefault(h.category, []).append(h)
    return {c: v for c, v in out.items() if v}
