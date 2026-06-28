# rag/integrate_oldyears.py
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
#   uv run python rag/integrate_oldyears.py            # map (checkpoint)
#   uv run python rag/integrate_oldyears.py --apply    # clean/dedup 에 추가
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

try:
    from rag.extract import get_client
    from rag.standardize import (_batch_prompt, _call_standardize, Instance,
                                 BATCH_SIZE, LONG_CSV_COLUMNS)
except ImportError:
    from extract import get_client
    from standardize import (_batch_prompt, _call_standardize, Instance,
                            BATCH_SIZE, LONG_CSV_COLUMNS)

try:
    from rag.paths import OUTPUT_DIR
except ImportError:
    from paths import OUTPUT_DIR
STAGING = OUTPUT_DIR / "_staging_oldyears"
CLEAN = OUTPUT_DIR / "standardized_long.clean.csv"
DEDUP = OUTPUT_DIR / "standardized_long.dedup.csv"
CLEAN_COLUMNS = LONG_CSV_COLUMNS[:5] + ["std_response_label"] + LONG_CSV_COLUMNS[5:]


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
    rows: list[dict] = []
    for r in records:
        sid = qmap.get((r.get("source"), r.get("subsection")), "")
        entry = dictionary.get(sid, {})
        base = {
            "std_id": sid, "std_label": entry.get("std_label", ""),
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

    client = get_client()
    qmap, dictionary = map_to_stdids(client, questions, seed)

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
    print(f"\n✅ clean.csv·dedup.csv 에 2022 행 {len(rows)}개 추가 (기존 행 보존)")


if __name__ == "__main__":
    main()
