# rag/curate/integrate_oldyears.py
# -----------------------------------------------------------------------------
# 옛 연도(비전 추출) 데이터를 기존 큐레이션 데이터셋에 '증분' 통합한다.
#
# 왜 증분인가(중요):
#   - standardize.py 를 통째로 다시 돌리면 std_id 사전을 LLM 으로 '처음부터' 재생성해
#     기존 2023~25 의 std_id 가 비결정적으로 바뀐다 → corrections·std_aliases·routing·
#     restore·eval·테스트가 전부 깨진다. (실측: 재실행 시 39개 std_id 가 사라짐)
#   - 그래서 기존 std_id 사전을 '시드(고정)'로 주고, 새 연도(2022) 문항만 그 사전에
#     매핑한다(있으면 기존 std_id 로 연결, 없으면 새로 추가). 기존 행은 건드리지 않는다.
#
# 두 단계:
#   1) map  : 2022 문항 → std_id 매핑만 하고 결과를 보여준다(파일 변경 없음). [기본]
#   2) apply: 매핑으로 2022 행을 만들어 clean.csv·dedup.csv 에 '추가'한다(라이브 반영).
#
# 실행:
#   uv run python -m rag.curate.integrate_oldyears            # map (checkpoint)
#   uv run python -m rag.curate.integrate_oldyears --apply    # clean/dedup 에 추가
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from rag.ingest.extract import get_client
from rag.transform.standardize import (_batch_prompt, _call_standardize, Instance,
                             BATCH_SIZE, LONG_CSV_COLUMNS)
from rag.core.paths import OUTPUT_DIR
STAGING = OUTPUT_DIR / "_staging_oldyears"
CLEAN = OUTPUT_DIR / "standardized_long.clean.csv"
DEDUP = OUTPUT_DIR / "standardized_long.dedup.csv"
MAP_FILE = STAGING / "std_mapping.json"     # 결정적 매핑(저장/재사용)
# 사람이 채운 과병합 교정 워크시트(curation/). proposed_std_id 가 채워진 행만
# (year, current_std_id, subsection 접두사) 로 std_id 를 결정적으로 교정한다.
WORKSHEET = Path(__file__).resolve().parent.parent / "curation" / "mapping_review.csv"
CLEAN_COLUMNS = LONG_CSV_COLUMNS[:5] + ["std_response_label"] + LONG_CSV_COLUMNS[5:]

# 매핑 정제: LLM 이 '다른 문항'을 같은 std_id 로 과병합하는 것을 문항(subsection)
# 텍스트로 강제 교정한다. LLM 이 탄소성적표지를 저탄소제품/환경성적표지/탄소성적표지로
# 제각각 매핑하고 인지도/인지경로/정의인지를 뒤섞어서, 주제+유형으로 결정한다.

# override 로 새로 생기는 std_id 의 표시 라벨(시드 사전에 없는 것).
STDID_OVERRIDE_LABELS: dict[str, str] = {
    "친환경제품_관심증가": "친환경제품 관심 증가(전년 대비)",
}


def _override_stdid(subsection: str, mapped: str) -> str:
    """ 문항 텍스트로 std_id 를 교정(과병합 분리). 매치 없으면 매핑값 그대로. """
    q = subsection or ""
    # ① '관심 증가'(전년 대비 변화) ≠ '관심도'(척도)
    if "관심" in q and any(k in q for k in ("늘었", "증가", "예전보다")):
        return "친환경제품_관심증가"
    # ② 탄소성적표지·탄소발자국(=환경성적표지) vs 저탄소제품(마크) 인지 계열을
    #    주제 + 유형(인지경로/정의인지/인지도)으로 분리. (LLM 이 뒤섞은 것 교정)
    is_low = "저탄소제품" in q
    is_epd = (("탄소성적표지" in q) or ("탄소발자국" in q)) and not is_low
    if is_low or is_epd:
        subj = "저탄소제품" if is_low else "환경성적표지"
        if "경로" in q:
            return f"{subj}_인지경로"
        if any(k in q for k in ("제도 인지", "정의", "어떤 제도", "라고 생각")):
            return f"{subj}_정의인지"
        if any(k in q for k in ("인지", "알고", "로고")):
            return f"{subj}_인지도"
    return mapped


def load_worksheet_overrides() -> list[dict]:
    """ mapping_review.csv 에서 proposed_std_id 가 채워진 행만 읽는다.
        반환 항목: {year, current_std_id, prefix(잘린 subsection), proposed} """
    if not WORKSHEET.exists():
        return []
    overrides: list[dict] = []
    with open(WORKSHEET, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            proposed = (r.get("proposed_std_id(편집)") or "").strip()
            sub = (r.get("subsection") or "").strip()
            if proposed and sub:
                overrides.append({"year": str(r.get("year") or "").strip(),
                                  "current_std_id": (r.get("current_std_id") or "").strip(),
                                  "prefix": sub, "proposed": proposed})
    return overrides


def _apply_worksheet(year, subsection: str, sid: str, overrides: list[dict]) -> str:
    """ 워크시트 교정을 적용한다. (year, 현재 std_id, subsection 접두사) 가 맞으면
        proposed std_id 로 바꾼다. 접두사가 여럿 맞으면 가장 긴 것을 택한다. """
    best = None
    for ov in overrides:
        if (ov["year"] == str(year) and ov["current_std_id"] == sid
                and (subsection or "").startswith(ov["prefix"])):
            if best is None or len(ov["prefix"]) > len(best["prefix"]):
                best = ov
    return best["proposed"] if best else sid


def load_curated_dict() -> dict[str, dict]:
    """ 기존 큐레이션 clean.csv 에서 std_id 사전(시드)을 만든다. """
    seed: dict[str, dict] = {}
    with open(CLEAN, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sid = (r.get("std_id") or "").strip()
            if sid and sid not in seed:
                seed[sid] = {"std_id": sid, "std_label": r.get("std_label", ""),
                             "category": r.get("category", "")}
    return seed


def load_staged() -> list[dict]:
    """ 스테이징된 옛 연도 추출 레코드(비전)를 모두 읽는다. """
    recs: list[dict] = []
    for p in sorted(STAGING.glob("*.extracted.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
    return recs


def distinct_questions(records: list[dict]) -> list[dict]:
    """ (source, subsection) 로 중복 제거한 대표 문항 레코드들(매핑 단위). """
    seen: dict[tuple, dict] = {}
    for r in records:
        key = (r.get("source"), r.get("subsection"))
        if key not in seen:
            seen[key] = r
    return list(seen.values())


def map_to_stdids(client, questions: list[dict], seed: dict[str, dict]):
    """ 시드 사전을 고정으로 주고 2022 문항을 std_id 에 매핑한다.
        반환: (qmap{(source,subsection):std_id}, dictionary(시드+신규)) """
    dictionary = {k: dict(v) for k, v in seed.items()}    # 시드 복사(기존 보존)
    instances = [Instance(id=i, record=r) for i, r in enumerate(questions)]
    qmap: dict[tuple, str] = {}
    for bi in range((len(instances) + BATCH_SIZE - 1) // BATCH_SIZE):
        batch = instances[bi * BATCH_SIZE:(bi + 1) * BATCH_SIZE]
        result = _call_standardize(client, _batch_prompt(dictionary, batch))
        for entry in result.get("new_entries", []):
            sid = entry.get("std_id")
            if sid and sid not in dictionary:
                dictionary[sid] = {"std_id": sid,
                                   "std_label": entry.get("std_label", sid),
                                   "category": entry.get("category", "")}
        for a in result.get("assignments", []):
            inst = instances[a["id"]] if a.get("id") is not None and a["id"] < len(instances) else None
            if inst and a.get("std_id"):
                rec = inst.record
                qmap[(rec.get("source"), rec.get("subsection"))] = a["std_id"]
    return qmap, dictionary


def build_rows(records: list[dict], qmap: dict[tuple, str],
               dictionary: dict[str, dict]) -> list[dict]:
    """ 2022 레코드 → clean.csv 형식 행들(응답 항목 펼침). std_response_label=정제 라벨. """
    overrides = load_worksheet_overrides()
    rows: list[dict] = []
    for r in records:
        sid = qmap.get((r.get("source"), r.get("subsection")), "")
        sid = _override_stdid(r.get("subsection") or "", sid)   # 과병합 교정(하드코딩)
        base = dictionary.get(sid, {})                          # 교정 前 기존 항목
        new_sid = _apply_worksheet(r.get("year"), r.get("subsection") or "",
                                   sid, overrides)              # 워크시트 교정(사람확정)
        if new_sid != sid and new_sid not in dictionary:        # 새 std_id 면 항목 합성
            label = base.get("std_label") or new_sid
            if new_sid.endswith("_복수응답") and label:
                label = label + " (1+2+3순위 복수응답)"
            elif label == base.get("std_label"):                # 같은 라벨이면 식별 위해 id 사용
                label = new_sid
            dictionary[new_sid] = {"std_id": new_sid, "std_label": label,
                                   "category": base.get("category", "")}
        sid = new_sid
        entry = dictionary.get(sid, {})
        std_label = entry.get("std_label") or STDID_OVERRIDE_LABELS.get(sid, sid)
        base = {
            "std_id": sid, "std_label": std_label,
            "category": entry.get("category", ""), "year": r.get("year"),
            "unit": r.get("unit"), "base_n": r.get("base_n"),
            "multi_response": r.get("multi_response"),
            "question_summary": r.get("question_summary"),
            "section": r.get("section"), "subsection": r.get("subsection"),
            "prev_year_note": r.get("prev_year_note"), "source": r.get("source"),
            "page_start": r.get("page_start"), "page_end": r.get("page_end"),
            "extraction_confidence": r.get("extraction_confidence"),
            "warning": r.get("warning"), "figures": " / ".join(r.get("figures", [])),
        }
        for it in (r.get("response_items") or []):
            label = (it.get("label") or "").strip()
            rows.append({**base, "response_label": label,
                         "std_response_label": label, "value": it.get("value")})
    return rows


def _append_rows(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def _dedup_in_place(path: Path, columns: list[str]) -> int:
    """ (year, std_id, std_response_label) 중복 행을 제거(값 있는 행 우선, 먼저 것 유지).
        2015~2022는 보고서가 2종이라 같은 설문 문항이 두 번 들어옴 → 한 해로 통합.
        반환: 제거된 행 수. 값이 서로 다른 충돌은 경고로 남긴다. """
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    seen: dict[tuple, dict] = {}
    order: list[tuple] = []
    removed = 0
    for r in rows:
        key = (r.get("year"), r.get("std_id"),
               (r.get("std_response_label") or r.get("response_label") or "").strip())
        if key not in seen:
            seen[key] = r
            order.append(key)
            continue
        removed += 1
        prev = seen[key]
        pv = (prev.get("value") or "").strip()
        cv = (r.get("value") or "").strip()
        if not pv and cv:                 # 먼저 것이 빈값이면 값 있는 것으로 교체
            seen[key] = r
        elif pv and cv and pv != cv:      # 둘 다 값 있는데 다르면 충돌(같은 설문이면 동일해야)
            print(f"  ⚠️ 값 충돌 {key}: {pv} vs {cv} (먼저 값 유지)")
    if removed:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            for key in order:
                w.writerow({c: seen[key].get(c, "") for c in columns})
    return removed


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    apply = "--apply" in sys.argv

    seed = load_curated_dict()
    records = load_staged()
    questions = distinct_questions(records)
    print(f"시드 std_id {len(seed)}개 | 스테이징 레코드 {len(records)} | 고유 문항 {len(questions)}")

    # 결정적 매핑: 저장된 매핑이 있으면 재사용(LLM 재호출·비결정성 제거).
    if MAP_FILE.exists():
        saved = json.loads(MAP_FILE.read_text(encoding="utf-8"))
        qmap = {tuple(k.split("\t")): v for k, v in saved["qmap"].items()}
        dictionary = saved["dictionary"]
        print(f"♻️ 저장된 매핑 재사용: {MAP_FILE.name} ({len(qmap)}문항)")
    else:
        client = get_client()
        qmap, dictionary = map_to_stdids(client, questions, seed)
        MAP_FILE.write_text(json.dumps(
            {"qmap": {f"{k[0]}\t{k[1]}": v for k, v in qmap.items()},
             "dictionary": dictionary}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"💾 매핑 저장: {MAP_FILE.name} ({len(qmap)}문항)")

    existing = sum(1 for v in qmap.values() if v in seed)
    new = sorted({v for v in qmap.values() if v not in seed})
    print(f"\n매핑 결과: 기존 std_id 연결 {existing} | 신규 std_id {len(new)}")
    print("\n[기존 std_id 로 연결된 2022 문항 = 추세 연결됨]")
    for (src, sub), sid in sorted(qmap.items(), key=lambda x: x[1]):
        if sid in seed:
            print(f"  {sub[:34]:34s} → {sid}")
    print("\n[신규 std_id (옛 연도에만 있는 문항)]")
    for sid in new:
        subs = [sub for (s, sub), v in qmap.items() if v == sid]
        print(f"  {sid}  ← {subs[0][:40] if subs else ''}")

    if not apply:
        print("\n(map 단계 — 파일 변경 없음. 적용하려면 --apply)")
        return

    rows = build_rows(records, qmap, dictionary)
    _append_rows(CLEAN, rows, CLEAN_COLUMNS)
    _append_rows(DEDUP, rows, CLEAN_COLUMNS)
    print(f"\n✅ clean.csv·dedup.csv 에 행 {len(rows)}개 추가 (기존 행 보존)")
    # 두 보고서(친환경제품+탄소/그린카드)가 겹치는 문항을 한 해로 통합(중복 제거).
    rc = _dedup_in_place(CLEAN, CLEAN_COLUMNS)
    rd = _dedup_in_place(DEDUP, CLEAN_COLUMNS)
    print(f"♻️ 중복 통합: clean -{rc} · dedup -{rd} 행 ((year,std_id,라벨) 기준)")


if __name__ == "__main__":
    main()
