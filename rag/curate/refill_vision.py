# rag/curate/refill_vision.py
# -----------------------------------------------------------------------------
# 빵구(표 추출 실패) 블록을 '비전'으로 다시 읽어 데이터에 채워 넣는다.
#
# 흐름:
#   1) standardized_long.csv 에서 '빵구 의심 블록'을 고른다.
#      (값이 빈 행이 있거나, 단일응답+% 인데 보기 합계가 100 에서 크게 벗어남)
#   2) 각 블록의 원본 PDF 페이지를 extract_vision 으로 다시 읽는다(이미지→멀티모달).
#   3) 비전이 읽은 (라벨→값)을 기존 행에 맞춰 반영한다:
#        - 빈칸이면 값을 채우고(fill), 값이 다르면 고친다(change).
#        - 기존에 아예 없던 라벨이면 새 행으로 추가(inject).
#      std_id / 표준라벨(refine 결과)은 건드리지 않는다(값만 보정 + 누락행 주입).
#   4) standardized_long.csv 와 clean.csv 를 갱신하고, 무엇을 바꿨는지 로그로 남긴다.
#      (이후 dedup → flags → review 를 다시 돌리면 끝)
#
# 실행:
#   uv run python -m rag.curate.refill_vision                 # 빵구 블록 전부
#   uv run python -m rag.curate.refill_vision 친환경제품_확대희망품목   # 특정 std_id 만(검증용)
#   uv run python -m rag.curate.refill_vision --dry            # 바꾸지 않고 미리보기
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

from rag.ingest.extract_vision import extract_pages_vision
from rag.ingest.extract import get_client
from rag.core.paths import OUTPUT_DIR
from rag.core.logging_setup import setup_logging

# 지역 리스트 'log'(액션 로그)와 이름이 겹치지 않게 'logger'
logger = logging.getLogger("refill_vision")

RAW_CSV = OUTPUT_DIR / "standardized_long.csv"          # 3단계 산출(원본 라벨)
CLEAN_CSV = OUTPUT_DIR / "standardized_long.clean.csv"  # 4.1 산출(+std_response_label)
LOG_CSV = OUTPUT_DIR / "vision_refill_log.csv"
CANDIDATES_CSV = OUTPUT_DIR / "vision_candidates.csv"   # 검토 후보(사람이 출처 보고 확정)

# ★원칙: "추측은 데이터가 아니다"★
# 비전 재추출 결과를 정형 CSV(canonical)에 자동 반영하지 않는다.
# 대신 '검토 후보'(vision_candidates.csv)로만 내보내고, 사람이 검수 탭에서
# 출처를 보고 corrections.jsonl 로 확정한 것만 데이터가 된다.
CANDIDATE_MODE = True

SUM_OFF = 10.0   # 단일응답+% 블록 합계가 100 에서 이만큼 벗어나면 빵구 의심

# 표 머리글/집계행 — 품목이 아니므로 주입하지 않는다(정규화 후 정확 일치만).
STOP_LABELS = {
    "전체", "사례수", "구분", "구분계속", "소계", "합계", "계",
    "top", "top2", "top3", "인지", "비인지", "평균",
}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def norm(s: str) -> str:
    """ 라벨 비교용 정규화: 공백/쉼표/가운뎃점/괄호 제거 + 소문자. """
    s = (s or "")
    for ch in " \t,()·ㆍ・/":
        s = s.replace(ch, "")
    return s.lower()


def sim(a: str, b: str) -> float:
    """ 두 라벨의 글자 2-그램 자카드 유사도(0~1). 철자 미세차(하는/한, 띄어쓰기)도 높게 나온다. """
    na, nb = norm(a), norm(b)
    A = {na[i:i + 2] for i in range(len(na) - 1)}
    B = {nb[i:i + 2] for i in range(len(nb) - 1)}
    if not A or not B:
        return 1.0 if na == nb else 0.0
    return len(A & B) / len(A | B)


FUZZY_TH = 0.5   # 이 이상이면 같은 보기로 보고 빈칸을 채운다(평행 행 주입 방지)


def load(path: Path) -> tuple[list[dict], list[str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def save(path: Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


# 빈칸이 이 비율을 넘으면 '표 자체가 깨진 것'으로 보고 비전을 권위값으로 전체 보정한다.
# 그 미만이면 기존 값(서술문에서 뽑힌 신뢰값)은 두고 '빈칸만' 채운다.
FULL_REFILL_RATIO = 0.5


def find_hole_blocks(raw: list[dict]) -> list[tuple]:
    """ '빈칸이 1개 이상' 인 블록만 빵구로 본다.
        (합계만 100에서 벗어난 블록은 대부분 복수응답 오탐이므로 건드리지 않는다.) """
    grp: dict[tuple, list[dict]] = defaultdict(list)
    for r in raw:
        grp[(r.get("year"), r.get("std_id"))].append(r)
    return [key for key, rows in grp.items()
            if any(num(r.get("value")) is None for r in rows)]


def reconcile_block(client, key, raw, clean, raw_cols, clean_cols, log, dry):
    """ 한 블록을 비전으로 재추출해 raw/clean 행에 반영한다(in-place).

    보수적 정책(멀쩡한 데이터를 깨지 않기 위해):
      - 빈칸 채움(fill): 모든 블록에서 허용(빈칸은 명백한 빵구).
      - 값 교정(change): '단일응답(%)' 블록에서만. (복수응답은 서술값이 맞을 수 있어 건드리지 않음)
      - 누락행 주입(inject): 기존 라벨과 거의 같은(부분일치) 라벨은 건너뜀(중복 방지).
    """
    year, std_id = key
    raw_rows = [r for r in raw if r.get("year") == year and r.get("std_id") == std_id]
    clean_rows = [r for r in clean if r.get("year") == year and r.get("std_id") == std_id]
    if not raw_rows:
        return

    # 빈칸 비율로 '표가 통째로 깨졌는지' 판단한다.
    blanks = sum(1 for r in raw_rows if num(r.get("value")) is None)
    ratio = blanks / len(raw_rows) if raw_rows else 0
    full_refill = ratio > FULL_REFILL_RATIO   # True 면 비전을 권위값으로 전체 보정

    sample = raw_rows[0]
    source = sample.get("source")
    pages = [int(r["page_start"]) for r in raw_rows if r.get("page_start", "").isdigit()]
    pages += [int(r["page_end"]) for r in raw_rows if r.get("page_end", "").isdigit()]
    ps, pe = (min(pages), max(pages)) if pages else (0, 0)
    context = f"{sample.get('section','')} > {sample.get('subsection','')}\n{sample.get('question_summary','')}"
    focus = sample.get("figures", "") or ""

    data = extract_pages_vision(client, source, ps, pe, context=context, focus=focus)
    items = data.get("response_items", [])
    vmap: dict[str, tuple[str, float]] = {}
    for it in items:
        lab = (it.get("label") or "").strip()
        val = num(it.get("value"))
        nlab = norm(lab)
        if not lab or val is None or nlab in STOP_LABELS:
            continue
        vmap[nlab] = (lab, val)

    kind = f"전체보정(빈칸{blanks}/{len(raw_rows)})" if full_refill else f"빈칸만(빈칸{blanks}/{len(raw_rows)})"
    print(f"  [{year} {std_id}] p{ps}-{pe} {kind} → 비전 품목 {len(vmap)}개 (conf={data.get('extraction_confidence')})")
    if not vmap:
        log.append({"year": year, "std_id": std_id, "action": "vision_empty",
                    "label": "", "old": "", "new": "", "detail": data.get("warning", "")})
        return

    vitems = [(lab, val, nlab) for nlab, (lab, val) in vmap.items()]
    used_keys: set[str] = set()   # 소비된 비전 품목(정규화 라벨) — 주입 중복 방지

    def prefix_len(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a, b):
            if x == y:
                n += 1
            else:
                break
        return n

    def same_option(a: str, b: str) -> bool:
        """ 두 정규화 라벨이 '같은 보기'인가: 2-그램 유사도 또는 긴 공통 접두사. """
        return sim(a, b) >= FUZZY_TH or (min(len(a), len(b)) >= 6 and prefix_len(a, b) >= 6)

    # 블록의 고유 기존 라벨(등장 순)
    existing_labels: list[str] = []
    seen = set()
    for r in raw_rows:
        el = r.get("response_label")
        if el not in seen:
            seen.add(el)
            existing_labels.append(el)

    # 라벨별 결정: label -> (newv, action, old_value)
    decision: dict[str, tuple] = {}
    for el in existing_labels:
        nel = norm(el)
        if not nel:
            continue                                   # 빈 라벨 행은 매칭 대상 아님(아래서 처리)
        cur = next((num(r.get("value")) for r in raw_rows if r.get("response_label") == el), None)

        hit = None
        consumed = None
        if nel in vmap:                                # 1) 정확 매칭
            hit = vmap[nel]
            consumed = nel
        else:                                          # 2) 퍼지/접두사 매칭
            best, bs = None, 0.0
            for lab, val, vnl in vitems:
                if vnl in used_keys:
                    continue
                if not same_option(nel, vnl):
                    continue
                s = sim(nel, vnl)
                if s >= bs:
                    bs, best = s, (lab, val, vnl)
            if best:
                hit = (best[0], best[1])
                consumed = best[2]
        if hit is None:
            continue

        _, vval = hit
        used_keys.add(consumed)
        old_value = "" if cur is None else next((r.get("value") for r in raw_rows if r.get("response_label") == el), "")
        newv = ("%g" % vval)
        if cur is None:
            action = "fill"
        elif abs(cur - vval) >= 0.05:
            action = "change" if full_refill else "discrepancy"   # 깨진 표만 교정, 아니면 검토
        else:
            continue
        decision[el] = (newv, action, old_value)

    # --- 구조 불일치 게이트 ---
    # 기존 라벨이 있는데 비전과 '하나도' 안 맞으면, 축이 다른 문항(예: 행동×척도 행렬)을
    # 비전이 다르게 읽은 것 → 건드리면 망가지므로 블록 전체를 건너뛴다(사람 검토용 기록).
    nonempty_labels = [e for e in existing_labels if norm(e)]
    if nonempty_labels and not decision:
        log.append({"year": year, "std_id": std_id, "action": "structure_mismatch",
                    "label": "", "old": "", "new": "",
                    "detail": f"p{ps}-{pe} 기존 라벨과 비전이 전혀 안 맞음 — 행렬형 등, 보류"})
        print("    ↳ 구조 불일치 → 보류(변경 없음)")
        return

    # 결정을 raw/clean 양쪽에 적용
    for rows in (raw_rows, clean_rows):
        for r in rows:
            d = decision.get(r.get("response_label"))
            if d and d[1] != "discrepancy" and not dry:
                r["value"] = d[0]
    for el, (newv, action, old_value) in decision.items():
        log.append({"year": year, "std_id": std_id, "action": action, "label": el,
                    "old": old_value if action in ("change", "discrepancy") else "",
                    "new": newv, "detail": f"p{ps}-{pe} ({kind})"})

    # 주입: 비전엔 있으나 소비되지 않은 품목 (기존 라벨과 같은 보기면 제외)
    existing_norms = [norm(r.get("response_label")) for r in raw_rows]
    existing_norms += [norm(r.get("std_response_label")) for r in clean_rows]
    existing_norms = [e for e in existing_norms if e]

    for lab, vval, nlab in vitems:
        if nlab in used_keys or any(same_option(nlab, e) for e in existing_norms):
            continue
        newv = ("%g" % vval)
        if not dry:
            nr = dict(raw_rows[0])
            nr["response_label"] = lab
            nr["value"] = newv
            raw.append(nr)
            nc = dict(clean_rows[0] if clean_rows else raw_rows[0])
            nc["response_label"] = lab
            nc["std_response_label"] = lab
            nc["value"] = newv
            for c in clean_cols:
                nc.setdefault(c, "")
            clean.append(nc)
        log.append({"year": year, "std_id": std_id, "action": "inject",
                    "label": lab, "old": "", "new": newv, "detail": f"p{ps}-{pe} 누락행 추가 ({kind})"})

    # 빈 라벨('') 행만 제거: 라벨조차 못 뽑힌 추출 쓰레기. (실제 라벨 행은 절대 삭제 안 함)
    for rows in (raw_rows, clean_rows):
        for r in rows:
            if not norm(r.get("response_label")):
                r["_drop"] = "1"
                if rows is raw_rows:
                    log.append({"year": year, "std_id": std_id, "action": "drop_empty",
                                "label": "", "old": "", "new": "",
                                "detail": f"p{ps}-{pe} 빈 라벨 행 제거"})
                    if rows is raw_rows:
                        log.append({"year": year, "std_id": std_id, "action": "drop_artifact",
                                    "label": r.get("response_label"), "old": "", "new": "",
                                    "detail": f"p{ps}-{pe} 비전 미매칭 빈칸행 제거 ({kind})"})


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    setup_logging("refill_vision")   # 구조화 로그가 run 로그(stderr 캡처)·자체 파일에 남게 한다.

    args = sys.argv[1:]
    dry = "--dry" in args
    args = [a for a in args if a != "--dry"]
    only_std = args[0] if args else None

    # 테스트/검증 모드(RAG_FAKE_LLM): 비전 호출 없이 즉시 종료(무료·결정적, E2E 안전).
    # canonical CSV·기존 후보 파일은 건드리지 않는다(추측을 지어내지 않는다).
    if os.getenv("RAG_FAKE_LLM"):
        print("RAG_FAKE_LLM — 비전 재판독 건너뜀(스텁). 후보 생성 안 함.")
        logger.info("refill_vision fake 모드 — 건너뜀(비전 호출 0, 후보 0)")
        return

    raw, raw_cols = load(RAW_CSV)
    clean, clean_cols = load(CLEAN_CSV)

    holes = find_hole_blocks(raw)
    if only_std:
        holes = [k for k in holes if k[1] == only_std]
    logger.info("refill_vision 시작 — 빵구 블록 %d개%s", len(holes), " (dry)" if dry else "")
    print(f"빵구 블록 {len(holes)}개 비전 재추출 시작 {'(미리보기)' if dry else ''}\n")

    client = get_client()
    log: list[dict] = []
    for key in holes:
        reconcile_block(client, key, raw, clean, raw_cols, clean_cols, log, dry)

    # --- 저장 ---
    if not dry:
        log_cols = ["year", "std_id", "action", "label", "old", "new", "detail"]
        with open(LOG_CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=log_cols)
            w.writeheader()
            w.writerows(log)

        if CANDIDATE_MODE:
            # canonical CSV 는 건드리지 않는다. 비전 결과를 '검토 후보'로만 내보낸다.
            import re
            ysrc = {}
            for r in raw:
                ysrc.setdefault(r.get("year"), r.get("source", ""))
            slabel = {(r.get("year"), r.get("std_id")): r.get("std_label", "") for r in clean}

            def _page(detail):
                m = re.search(r"p(\d+(?:-\d+)?)", detail or "")
                return m.group(1) if m else ""

            cand = [{
                "year": a["year"], "std_id": a["std_id"],
                "std_label": slabel.get((a["year"], a["std_id"]), ""),
                "response_label": a["label"], "action": a["action"],
                "old_value": a["old"], "vision_value": a["new"],
                "source": ysrc.get(a["year"], ""), "page": _page(a["detail"]),
                "method": "vision", "status": "candidate",
            } for a in log if a["action"] in ("fill", "change", "inject", "discrepancy")]
            ccols = ["year", "std_id", "std_label", "response_label", "action",
                     "old_value", "vision_value", "source", "page", "method", "status"]
            with open(CANDIDATES_CSV, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=ccols)
                w.writeheader()
                w.writerows(cand)
        else:
            # (비권장) 직접 반영 모드 — 아티팩트 제외하고 canonical 갱신
            raw = [r for r in raw if not r.get("_drop")]
            clean = [r for r in clean if not r.get("_drop")]
            save(RAW_CSV, raw, raw_cols)
            save(CLEAN_CSV, clean, clean_cols)

    # 요약
    from collections import Counter
    cnt = Counter(a["action"] for a in log)
    # 디버깅용 구조화 로그: 무엇을 시도/메움/남겼는지 한 줄로(run 로그 + 자체 로그에 남는다).
    filled = cnt.get("fill", 0) + cnt.get("change", 0) + cnt.get("inject", 0)
    logger.info(
        "refill_vision 완료 — 빵구블록 %d · 메움(fill/change/inject) %d · "
        "보류(구조불일치 %d, 검토대상 %d) · 비전실패 %d · 후보파일=%s",
        len(holes), filled, cnt.get("structure_mismatch", 0),
        cnt.get("discrepancy", 0), cnt.get("vision_empty", 0),
        "미저장(dry)" if dry else CANDIDATES_CSV.name,
    )
    print("\n" + "=" * 60)
    print(f"비전 재추출 반영: 빈칸 있는 블록 {len(holes)}개")
    print(f"  채움(fill)        : {cnt.get('fill',0)}  (빈칸을 비전값으로)")
    print(f"  교정(change)      : {cnt.get('change',0)}  (깨진 표 전체보정)")
    print(f"  주입(inject)      : {cnt.get('inject',0)}  (누락행 추가)")
    print(f"  빈라벨 제거(drop)  : {cnt.get('drop_empty',0)}  (라벨조차 없던 행)")
    print(f"  구조불일치 보류    : {cnt.get('structure_mismatch',0)}  (행렬형 등 — 손대지 않음)")
    print(f"  검토대상(discrepancy): {cnt.get('discrepancy',0)}  (값 다르나 보존 — 사람 검토)")
    if cnt.get("vision_empty"):
        print(f"  ⚠️ 비전 실패  : {cnt['vision_empty']} 블록")
    if not dry:
        if CANDIDATE_MODE:
            print(f"💾 검토 후보 → {CANDIDATES_CSV.name} (canonical CSV 는 건드리지 않음) | 로그 {LOG_CSV.name}")
            print("   → 사람이 검수 탭에서 출처 보고 corrections.jsonl 로 확정")
        else:
            print(f"💾 {RAW_CSV.name}, {CLEAN_CSV.name} 갱신 | 로그 {LOG_CSV.name}")
    else:
        print("(미리보기였음 — 저장 안 함)")
    print("=" * 60)


if __name__ == "__main__":
    main()
