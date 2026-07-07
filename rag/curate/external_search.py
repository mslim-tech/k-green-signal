# rag/curate/external_search.py
# -----------------------------------------------------------------------------
# 데이터 변화 지점(연도) → '외부 맥락 후보' 검색 — 현재는 과금 없는 스텁.
#
# 이 파일의 역할:
#   - 선택한 키워드 + 변화 연도로 외부 '검색어 후보'를 만든다(build_search_queries).
#   - 그 검색어로 외부 맥락 후보를 유형별(정책/제도·사회이슈·언론보도)로 돌려준다
#     (search_external_context). 지금은 실제 웹검색 API를 호출하지 않고, 사람이 이미
#     확정해 둔 curation/external_context.json 을 재료로 스텁 결과를 만든다(과금 0·결정적).
#
# 원칙(두 흐름의 '점선' = 추측 격리): 이 결과는 확정 '데이터'가 아니라 '참고할 만한
#   외부 맥락 후보'다. 정형 CSV 로 흘려보내지 않고 화면 표시 전용이며, 인과를 단정하지
#   않는다(그해 이런 맥락이 있었다는 참고일 뿐).
#
# TODO(실호출 붙이기): search_external_context 의 몸통을 OpenAI 웹검색 provider 로
#   교체한다 — build_search_queries 로 만든 문자열을 실제 검색에 넣고, 결과 기사들을
#   _classify 규칙 등으로 유형화해 ExternalHit(is_stub=False)로 반환한다. 재과금을 막게
#   질의→결과 캐시(디스크)를 앞단에 둔다. 실호출 전까지는 이 스텁으로 화면·구조만 검증.
# -----------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass

# 직접 실행(python rag/curate/external_search.py)과 패키지 import 를 모두 지원(프로젝트 관례).
try:
    from rag.curate.external_context import load_events
except ImportError:  # 스크립트로 직접 실행할 때
    from external_context import load_events


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
                            year_window: int = 1) -> list[ExternalHit]:
    """ (스텁) 검색어 후보로 외부 맥락 후보를 유형별로 돌려준다 — 과금 0·결정적.

        지금은 실제 웹검색을 호출하지 않고, 사람이 확정해 둔 external_context.json 을 재료로
        (대상 연도 ±year_window · 키워드 매치) 후보를 뽑아 유형만 분류한다. '진짜 검색 결과'가
        아니라 화면·구조를 먼저 검증하기 위한 데모용 결과다(모든 항목 is_stub=True).

        매칭은 프로젝트의 기존 관례(변곡점×외부맥락 패널)와 동일 — 각 사건의 match 태그 중
        하나라도 haystack(키워드+지표명 등)에 들어 있으면 관련으로 본다. haystack 이 비면
        keyword 로 대체한다.

        TODO(실호출): 이 함수 몸통을 OpenAI 웹검색 provider 로 교체한다(위 파일 상단 참고).
    """
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


def group_by_category(hits: list[ExternalHit]) -> dict[str, list[ExternalHit]]:
    """ 후보를 유형별로 묶는다(화면에서 유형별 섹션으로 렌더용). 순서는 CATEGORY_ORDER,
        비어 있는 유형은 뺀다. """
    out: dict[str, list[ExternalHit]] = {c: [] for c in CATEGORY_ORDER}
    for h in hits:
        out.setdefault(h.category, []).append(h)
    return {c: v for c, v in out.items() if v}
