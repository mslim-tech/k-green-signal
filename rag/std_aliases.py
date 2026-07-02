# rag/std_aliases.py
# -----------------------------------------------------------------------------
# 문항(std_id) 표준화 별칭 — 연도 간 '같은 문항이 다른 이름'으로 쪼개진 것을 통합
#
# 이 파일의 역할:
#   - 설문이 해마다 문항 표현을 바꾸면 같은 질문이 연도별로 다른 std_id 로
#     표준화돼 시계열이 끊긴다. 사람(도메인 전문가)이 "같은 문항"으로 확정한
#     쌍만 여기 별칭으로 적어 하나의 canonical std_id 로 합친다.
#   - corrections 와 같은 원칙: 자동(유사도)으로 합치지 않는다. 여기 표는
#     전부 사람이 확정한 것만 담는다("추측은 데이터가 아니다").
#
#   적용 지점: chunking.load_rows() 마지막(=corrections·skip·복원 이후). 그래서
#             인덱스(Q&A)와 신호등이 같은 통합 결과를 본다.
#
# 검증: uv run python -m pytest tests/test_std_aliases.py -q   (LLM 불필요·결정적)
#
# ── 확정 이력(2026-06-28, 사용자 mslim) ────────────────────────────────────────
#  #1 환경표지_구매이유(2023) → 환경표지_우선구매이유(2024·2025): 같은 문항.
#       ⚠️ 단, 2023은 단일응답(합 ~91%)·2024·25는 복수응답(합 300%+)이라 값은
#          추세 비교 불가 → std_id(문항 정체성)만 통합하고 응답 라벨은 정렬하지
#          않는다(합치면 단일↔복수 가짜 추세가 생김). Q&A 그룹핑용 통합.
#  #2 환경표지_재구매의향(2023) → 환경표지_우선구매의향(2025): 같은 문항.
#       단일 비율("의향 있음" 96.0% ↔ "구매 의향 있음" 93.8%)이라 라벨까지
#       정렬해 시계열 연결(2개년; 2024 미조사라 3개년은 아님).
# -----------------------------------------------------------------------------

from __future__ import annotations

# old std_id → canonical std_id (사람 확정 쌍만)
STD_ID_ALIASES: dict[str, str] = {
    "환경표지_구매이유": "환경표지_우선구매이유",   # #1
    "환경표지_재구매의향": "환경표지_우선구매의향",  # #2
}

# 용어 정규화: 같은 제도가 연도마다 다른 이름으로 불린 것을 한 용어로 통일한다.
#   탄소성적표지(2015~16)·탄소발자국(2017~19) = 환경성적표지(2020~)  (사용자 확정)
#   std_id·std_label 의 '부분 문자열'을 치환 → 그 제도 문항이 연도 간 같은 std_id 로 모임.
#   ('저탄소제품'은 이 용어를 포함하지 않으므로 영향 없음.)
STD_ID_TERM_MAP: dict[str, str] = {
    "탄소성적표지": "환경성적표지",
    "탄소발자국": "환경성적표지",
}

# 용어정규화 제외 std_id: 2017 보고서는 환경성적표지 로고(우산, p79)와 별개로
# 탄소발자국(구 탄소성적표지) 마크 인지도(p86)를 따로 조사했다. 후자를 별도 계열로
# 유지하려면(사용자 확정) 이 id 만 탄소발자국→환경성적표지 치환에서 제외한다.
# (2017 A행 6개에만 영향. 다른 연도 탄소발자국 인지도는 이미 환경성적표지_인지도 로 매핑됨.)
STD_ID_TERM_EXEMPT: set[str] = {"탄소발자국_인지도"}


def _normalize_terms(text: str) -> str:
    for old, new in STD_ID_TERM_MAP.items():
        if old in text:
            text = text.replace(old, new)
    return text

# canonical std_id → 통합 후 보여줄 std_label(있으면 그 라벨로 통일)
STD_LABEL_CANON: dict[str, str] = {
    "환경표지_우선구매이유": "환경표지 인증제품 우선 구매 이유",
    "환경표지_우선구매의향": "환경표지 인증제품 우선 구매 의향",
}

# std_id → {응답라벨 변형: 통일(canonical) 응답라벨}
#   연도 간 시대가 달라 같은 보기를 다르게 적은 것을 한 라벨로 모아 시계열을 잇는다.
#   - #2: 단일응답 이진 통일.
#   - 라벨 드리프트(2026-06-28): 옛(2018~22 '[관심]' 등 대괄호 집계)·최근(2023~25
#     '관심 있음(1+2)' 등) 긍정/부정 '집계'를 한 라벨로 통일(척도 보기는 그대로 둠).
#     집계가 원문에 이미 있는 문항만(인지도류=옛 집계 누락은 별도 도출 과제).
RESPONSE_LABEL_ALIASES: dict[str, dict[str, str]] = {
    "환경표지_우선구매의향": {"의향 있음": "구매 의향 있음"},   # #2
    "환경문제_관심도": {
        "[관심]": "관심 있음", "관심 있음(1+2)": "관심 있음", "관심 있다": "관심 있음",
        "[무관심]": "관심 없음", "관심 없음(3+4)": "관심 없음", "무관심하다": "관심 없음",
    },
    "친환경제품_관심도": {
        "[관심]": "관심 있음", "관심 있음(다소+매우)": "관심 있음", "관심 있다": "관심 있음",
        "[무관심]": "관심 없음", "관심 없음(별로+전혀)": "관심 없음", "무관심하다": "관심 없음",
    },
    "환경문제_민감도": {
        "[민감함]": "민감함", "민감함(3+4)": "민감함",
        "[민감하지 않음]": "민감하지 않음", "민감하지 않음(1+2)": "민감하지 않음",
    },
    "친환경제품_구매경험": {
        "있다": "구매 경험 있음", "녹색제품을 구매한 경험이 있다": "구매 경험 있음",
        "없다": "구매 경험 없음", "녹색제품을 구매한 경험이 없다": "구매 경험 없음",
    },
    "환경표지_전반신뢰도": {
        "[신뢰]": "신뢰", "신뢰한다(매우+다소)": "신뢰", "[불신]": "불신",
    },
    # 인지도: 최근 집계 라벨을 '인지'/'비인지'로 통일(옛은 아래 DERIVE 로 도출해 맞춤).
    "환경표지_인지도": {
        "인지함(잘 알고 있다+조금 알고 있다+본 적은 있다)": "인지",
        "비인지(전혀 모른다/처음 본다)": "비인지",
    },
}


# 옛 연도의 4점 척도(잘/조금/본 적은 있다/전혀 모른다)를 '23~25년 기준 척도'로 환산한다
# (사용자 확정). 23~25 기준: 인지/알고있다 = 잘 알고 있다+조금 알고 있다+본 적은 있다(top3),
# 비인지/모르고있다 = 전혀 모른다. (환경표지_인지도에 '인지함(잘+조금+본적)'으로 명시됨.)
#   도출 라벨은 각 문항의 23~25년 라벨과 똑같이 맞춰 시계열이 이어지게 한다.
#   std_id → {"label": 23~25 기준 라벨, "components": 합칠 옛 보기들}
_AWARE3 = ["잘 알고 있다", "조금 알고 있다", "본 적은 있다"]
# 2017 은 환경마크 로고가 신/구로 리뉴얼되어 인지도를 '신마크/구마크' 형식으로 조사했다
# (환경표지·환경성적표지 모두). 그 해의 '인지'는 어느 마크든 알아본 합(모두 인지+신마크만
# +구마크만), 비인지는 '신/구마크 모두 비인지'. 척도(잘/조금/본적) 대신 이 보기로 도출한다.
_AWARE_MARK = ["신마크/구마크 모두 인지", "신마크만 인지", "구마크만 인지"]
DERIVE_AGGREGATES: dict[str, dict] = {
    "환경표지_인지도": {"label": "인지", "components": _AWARE3,
                       "alt_components": [_AWARE_MARK]},
    # 환경성적표지도 2017 은 신/구마크 형식 → 같은 방식으로 인지 도출(2016 척도도 함께 연결).
    # (탄소발자국_인지도 2017 은 추출에 이미 '인지'/'비인지' 집계가 있어 도출 불필요.)
    "환경성적표지_인지도": {"label": "인지", "components": _AWARE3,
                          "alt_components": [_AWARE_MARK]},
    "그린카드_인지도": {"label": "알고 있다", "components": _AWARE3},
    "저탄소제품_인지도": {"label": "알고 있음", "components": _AWARE3},
    "녹색매장_인지도": {"label": "인지", "components": _AWARE3},
    # 관심도/민감도는 top2(매우+다소). 옛 일부 연도(2017)는 집계 행이 없어 도출.
    "환경문제_민감도": {"label": "민감함", "components": ["매우 민감", "다소 민감"]},
}


# 교차-시계열 백필(사람 확정): 설문이 문항명을 바꿔 새 문항이 최근 연도부터만 있을 때,
#   보고서가 "옛 연도는 다른(과거) 문항을 사용한다"고 명시한 경우에 한해 옛 연도를
#   그 과거 문항의 특정 보기 합으로 채워 시계열을 잇는다. 값은 과거 문항의 실제 추출값이라
#   지어내지 않으며, 근거(보고서 그림/표)를 명시한다. (from_std_id 는 원본 그대로 보존.)
#
#  #3 녹색제품_인지도: 문항은 '23년부터. 보고서(2023 그림 2-18, p.14)가 '19~'22년은
#     '환경표지(마크) 인지도'를 쓴다고 명시. 단 '인지도' 정의가 해마다 달라('등간 상이',
#     보고서 명시) '19~20년은 '잘 알고 있다'(top1), '21년부터 '잘+조금'(top2)이다.
#     → 그 정의대로 환경표지_인지도 보기를 합쳐 채운다(값이 그림 2-18과 정확히 일치).
SERIES_BACKFILL: dict[str, dict] = {
    "녹색제품_인지도": {
        "from_std_id": "환경표지_인지도",
        "std_label": "녹색제품 인지도",
        "category": "환경 인식",
        "response_label": "인지(잘 알고 있다+조금 알고 있다)",  # 2023~25 라벨과 동일
        "by_year": {
            "2019": ["잘 알고 있다"],
            "2020": ["잘 알고 있다"],
            "2021": ["잘 알고 있다", "조금 알고 있다"],
            "2022": ["잘 알고 있다", "조금 알고 있다"],
        },
        "note": ("녹색제품 인지도 시계열 연결: '19~'22는 환경표지(마크) 인지도 사용"
                 "(2023 보고서 그림 2-18, p.14). 인지도 정의 상이('19~20=잘 알고 있다, "
                 "'21~=잘+조금)."),
    },
}


def backfill_series(rows: list[dict]) -> list[dict]:
    """ SERIES_BACKFILL 설정대로 옛 연도 행을 과거 문항 보기 합으로 만들어 잇는다.
        - 대상(std_id)에 그 연도가 이미 있으면 건너뛴다(덮어쓰지 않음).
        - 필요한 보기가 그 해에 다 없으면 만들지 않는다(추측 금지).
        - 값 출처(source/page)는 과거 문항의 실제 행을 그대로 계승하고, 연결 근거는 warning 에 남긴다. """
    if not SERIES_BACKFILL:
        return rows
    # (from_std_id, year, 응답라벨) → (값, 표본행)
    comp: dict[tuple, tuple] = {}
    have_target: set[tuple] = set()   # (target_std_id, year) 이미 있는 것
    for r in rows:
        sid = (r.get("std_id") or "").strip()
        yr = (r.get("year") or "").strip()
        if sid in SERIES_BACKFILL:
            have_target.add((sid, yr))
        lbl = (r.get("std_response_label") or "").strip()
        try:
            val = float((r.get("value") or "").strip())
        except (TypeError, ValueError):
            continue
        comp[(sid, yr, lbl)] = (val, r)
    added: list[dict] = []
    for target, cfg in SERIES_BACKFILL.items():
        src_id = cfg["from_std_id"]
        for year, parts in cfg["by_year"].items():
            if (target, year) in have_target:          # 이미 있음 → 건너뜀
                continue
            picked = [comp.get((src_id, year, p)) for p in parts]
            if any(x is None for x in picked):          # 보기 하나라도 없으면 만들지 않음
                continue
            total = round(sum(v for v, _ in picked), 1)
            sample = picked[0][1]
            nr = dict(sample)
            nr["std_id"] = target
            nr["std_label"] = cfg["std_label"]
            nr["category"] = cfg["category"]
            nr["response_label"] = cfg["response_label"]
            nr["std_response_label"] = cfg["response_label"]
            nr["value"] = str(total)
            note = cfg.get("note", "")
            prev = (nr.get("warning") or "").strip()
            nr["warning"] = f"{prev} | {note}".strip(" |") if prev else note
            added.append(nr)
    return rows + added


def derive_aggregates(rows: list[dict]) -> list[dict]:
    """ 설정된 std_id 에 대해, 연도별로 구성 보기 합을 도출 라벨 행으로 추가한다
        (이미 그 라벨이 있으면 건너뜀 — 최근 연도는 통일된 집계가 이미 있음). """
    if not DERIVE_AGGREGATES:
        return rows
    from collections import defaultdict
    # (std_id, year) → {label: (value, sample_row)}
    by_key: dict[tuple, dict[str, tuple]] = defaultdict(dict)
    for r in rows:
        sid = (r.get("std_id") or "").strip()
        if sid in DERIVE_AGGREGATES:
            lbl = (r.get("std_response_label") or "").strip()
            try:
                val = float((r.get("value") or "").strip())
            except (TypeError, ValueError):
                continue
            by_key[(sid, r.get("year"))][lbl] = (val, r)
    added: list[dict] = []
    for (sid, year), labels in by_key.items():
        cfg = DERIVE_AGGREGATES[sid]
        if cfg["label"] in labels:           # 이미 집계 있음(최근) → 도출 불필요
            continue
        # 기본 보기집합이 다 없으면 대체 보기집합(예: 2017 신/구마크)을 차례로 시도
        candidate_sets = [cfg["components"]] + cfg.get("alt_components", [])
        comp_set = next((cs for cs in candidate_sets
                         if all(c in labels for c in cs)), None)
        if comp_set is None:                 # 어느 보기집합도 완전하지 않으면 도출 안 함
            continue
        comps = [labels[c] for c in comp_set]
        total = round(sum(v for v, _ in comps), 1)
        sample = comps[0][1]
        nr = dict(sample)
        nr["std_response_label"] = cfg["label"]
        nr["response_label"] = cfg["label"]
        nr["value"] = str(total)          # 다른 행과 같은 문자열 형식
        added.append(nr)
    return rows + added


def apply_aliases(rows: list[dict]) -> list[dict]:
    """ 행들의 std_id/std_label/std_response_label 을 확정 별칭으로 통일해 돌려준다.
        원본 리스트는 건드리지 않고 새 dict 리스트를 만든다(부수효과 최소화). """
    if not (STD_ID_ALIASES or STD_LABEL_CANON or RESPONSE_LABEL_ALIASES):
        return rows
    out: list[dict] = []
    for r in rows:
        sid = (r.get("std_id") or "").strip()
        exempt = sid in STD_ID_TERM_EXEMPT                       # 용어정규화 제외
        raw = STD_ID_ALIASES.get(sid, sid)
        canon = raw if exempt else _normalize_terms(raw)         # 별칭 + 용어 정규화
        label = (r.get("std_label") or "")
        label_canon = STD_LABEL_CANON.get(canon, label if exempt else _normalize_terms(label))
        lbl_map = RESPONSE_LABEL_ALIASES.get(canon)
        cur_resp = (r.get("std_response_label") or "").strip()
        resp_new = lbl_map.get(cur_resp) if lbl_map else None
        # 바뀐 게 없으면 원본 그대로(부수효과 최소화)
        if canon == sid and label_canon == label and resp_new is None:
            out.append(r)
            continue
        nr = dict(r)
        nr["std_id"] = canon
        nr["std_label"] = label_canon
        if resp_new is not None:
            nr["std_response_label"] = resp_new
        out.append(nr)
    return out
