# rag/curate/corrections.py
# -----------------------------------------------------------------------------
# 5단계 검수: 사람이 고친 내용(보정값)을 저장/불러오기/적용하기
#
# 이 파일의 역할:
#   - 검수 UI(app.py "검수" 탭)에서 사람이 확인/수정한 결과를 한 줄씩
#     outputs/corrections.jsonl 에 쌓는다. (원본 CSV 들은 절대 건드리지 않는다)
#   - 나중에 정제 파이프라인을 다시 돌릴 때(5.4) 이 보정값을 데이터에 덮어쓴다.
#
#   왜 JSONL(한 줄에 JSON 하나) 인가?
#     - 검수는 조금씩 여러 번 일어난다. append(이어쓰기)만 하면 되므로
#       기존 내용을 다시 쓸 필요가 없어 안전하고, 사람이 열어봐도 읽기 쉽다.
#     - 같은 행을 두 번 고치면 '나중에 쓴 것'이 이긴다(아래 latest_by_key).
#
#   레코드 하나(검수 1건)의 모양:
#     {
#       "year": "2024", "std_id": "...", "std_response_label": "...",  # 어느 행인지
#       "field": "value",            # 어느 컬럼을 본 것인지 (보통 value)
#       "old_value": "55.5",         # 검수 당시 원래 값 (추적용)
#       "new_value": "54.9",         # 사람이 고친 값 (status=fixed 일 때만 의미)
#       "status": "fixed",           # fixed=값 고침 / confirmed=원래 값 맞음 / skip=보류
#       "note": "그림 3-2 보고 정정", # 사람 메모(선택)
#       "reviewer": "mslim",         # 누가 했는지(선택)
#       "ts": "2026-06-26T10:00:00"  # 저장 시각(자동)
#     }
#
#   이 모듈은 표준 라이브러리(csv/json)만 쓴다. (정제 파이프라인에서도 재사용하므로
#   streamlit/pandas 같은 무거운 의존성을 넣지 않는다.)
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


from rag.core.paths import OUTPUT_DIR
CORRECTIONS_PATH = OUTPUT_DIR / "corrections.jsonl"

# 한 행을 가리키는 식별 키를 이루는 컬럼들.
# review_queue 의 중복키와 동일하게 (연도, 표준문항, 표준응답라벨) 로 행을 특정한다.
KEY_FIELDS = ("year", "std_id", "std_response_label")

# 검수 상태 값(허용 목록). UI 와 재정제가 같은 단어를 쓰도록 한 곳에 모은다.
STATUS_FIXED = "fixed"          # 값을 고쳤다 -> 재정제 때 new_value 로 덮어쓴다
STATUS_CONFIRMED = "confirmed"  # 원래 값이 맞다(검수 완료) -> 값은 그대로 둔다
STATUS_SKIP = "skip"            # 판단 보류 -> 아무것도 하지 않는다
# LLM 검증(adjudicate): LLM 이 원문(비전)과 독립 대조해 확정한 값. new_value 에 확정값을 담아
# fixed 처럼 반영한다. 사람 확정과 '신뢰 등급'을 구분하려고 상태를 따로 둔다(reviewer 에도 태깅).
# 사람이 이후 재검수하면 최신 레코드가 이겨 사람 판단이 우선한다(latest_by_key).
STATUS_LLM_VERIFIED = "llm_verified"
VALID_STATUSES = (STATUS_FIXED, STATUS_CONFIRMED, STATUS_SKIP, STATUS_LLM_VERIFIED)


def row_key(row: dict) -> tuple:
    """ 한 행(dict)에서 식별 키 튜플을 만든다. 공백은 정리한다. """
    return tuple((row.get(f) or "").strip() for f in KEY_FIELDS)


def _record_key(rec: dict) -> tuple:
    """ 저장된 보정 레코드에서 (식별 키 + field) 를 만든다.
        같은 행이라도 다른 컬럼(field)을 고쳤다면 별개로 본다. """
    return row_key(rec) + ((rec.get("field") or "value").strip(),)


def load_corrections(path: Path = CORRECTIONS_PATH) -> list[dict]:
    """ corrections.jsonl 을 한 줄씩 읽어 레코드 리스트로 돌려준다.
        파일이 없으면 빈 리스트. 깨진 줄은 건너뛴다(검수가 끊기지 않도록). """
    if not path.exists():
        return []
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # 손상된 한 줄 때문에 전체 검수가 멈추지 않도록 조용히 넘어간다.
                continue
    return records


def latest_by_key(records: list[dict] | None = None) -> dict[tuple, dict]:
    """ 같은 (행 + field) 에 대한 검수가 여러 번이면 '가장 마지막'만 남긴다.
        JSONL 은 시간 순서대로 append 되므로, 뒤에 나온 것이 최신이다. """
    if records is None:
        records = load_corrections()
    latest: dict[tuple, dict] = {}
    for rec in records:
        latest[_record_key(rec)] = rec   # 같은 키면 뒤(나중) 것으로 계속 덮어씀
    return latest


def reviewed_keys(records: list[dict] | None = None) -> set[tuple]:
    """ 이미 한 번이라도 검수(저장)된 행들의 식별 키 집합.
        UI 에서 '검수 완료' 표시를 하거나, 같은 행 재작업을 줄이는 데 쓴다. """
    if records is None:
        records = load_corrections()
    return {row_key(rec) for rec in records}


def add_correction(
    row: dict,
    status: str,
    new_value: str = "",
    field: str = "value",
    note: str = "",
    reviewer: str = "",
    path: Path = CORRECTIONS_PATH,
) -> dict:
    """ 검수 1건을 corrections.jsonl 에 한 줄 추가하고, 저장한 레코드를 돌려준다.

    매개변수:
      row       - 검수한 원본 행(dict). 여기서 식별 키와 old_value 를 뽑는다.
      status    - 'fixed' / 'confirmed' / 'skip' / 'llm_verified'(LLM 검증) 중 하나.
      new_value - status 가 'fixed'/'llm_verified' 일 때 반영되는 값. (그 외에는 빈 값이어도 됨)
      field     - 어떤 컬럼을 고쳤는지(기본 'value').
      note      - 사람이 남기는 메모(선택).
      reviewer  - 검수자 이름(선택).
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status 는 {VALID_STATUSES} 중 하나여야 합니다: {status!r}")

    record = {
        **{f: (row.get(f) or "").strip() for f in KEY_FIELDS},
        "field": field,
        "old_value": (row.get(field) or "").strip(),
        "new_value": str(new_value).strip(),
        "status": status,
        "note": note.strip(),
        "reviewer": reviewer.strip(),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }

    path.parent.mkdir(exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


_PAGE_RE = re.compile(r"p\.?\s*(\d+)")

# 검수로 복원하는 표의 '사람이 확인한' 라벨/요약(도메인 확정 설명).
# 추출이 깨져 표준 라벨/문항요약이 없으므로, 검색·리랭크가 비슷한 다른 표와
# 헷갈리지 않도록 명시한다. (값이 아니라 '이 표가 무엇인지'에 대한 설명이며,
# 도메인 전문가가 확인해 준 구분이다 — 지어낸 수치가 아니다.)
_RESTORED_TABLE_META: dict[tuple, dict] = {
    ("2023", "친환경제품_확대희망품목"): {
        "std_label": "친환경제품 확대 희망 품목 (표 3-60)",
        "question_summary": (
            "환경표지 인증제품(녹색제품)에 한정하지 않고 '친환경제품 전체'를 대상으로 "
            "향후 확대되길 희망하는 품목을 묻는 문항(표 3-60). "
            "'환경표지 인증제품 확대 희망 품목' 문항과는 별개의 표다."
        ),
    },
}


def confirmed_only_rows(existing_rows: list[dict],
                        path: Path = CORRECTIONS_PATH) -> list[dict]:
    """ 기존 행에 '대응 행이 없는' 사람 확정값(fixed/confirmed)을 인덱싱용 행으로 '복원'한다.

        두 경우를 함께 처리한다(둘 다 apply_corrections 로는 못 넣는 확정값):
          (a) 표 통째 누락 — 예: 2023 표 3-60 '친환경제품 확대 희망'. 2단 표라 추출이
              깨져 (year, std_id) 자체가 소스에서 사라졌다.
          (b) 기존 표의 '새 응답라벨' — 예: 비전 재판독으로 확정한 p.39/p.42 항목.
              같은 (year, std_id) 는 있지만 그 응답라벨 행은 없어(inject) 값이 못 들어간다.
        판정 기준을 (year, std_id) 존재가 아니라 '전체 키(year, std_id, std_response_label)
        존재'로 두어 (b) 도 복원한다. 이미 있는 행은 apply_corrections 가 담당하므로 건너뛴다.

        값 해석(apply_corrections 와 동일 의미):
          - fixed     → new_value (사람이 고쳐 넣은 값)
          - confirmed → old_value (사람이 '맞다'고 확인한 값)
          - skip / 빈값 → 제외(지어내지 않는다)
        메타(std_label/source/page/unit)는 같은 (year, std_id) 의 형제 행 → 같은 연도 →
        검수 메모(note 의 p.NN) 순으로 가져온다. 지어내지 않는다.
    """
    # 이미 있는 전체 키 — 이 키에 걸리는 확정값은 apply_corrections 가 처리하므로 제외.
    existing_full = {row_key(r) for r in existing_rows}
    # (year, std_id) 형제 행(메타 상속용)과 연도별 대표 source.
    sibling: dict[tuple, dict] = {}
    source_by_year: dict[str, str] = {}
    # 같은 (year, std_id) 그룹이 실제로 몇 페이지에 걸쳐 있는지(형제 행들의 page_start 집합).
    # 단일 페이지일 때만 그 페이지를 상속한다(여러 페이지면 새 라벨 위치가 불확실 → 지어내지 않음).
    sibling_pages: dict[tuple, set] = {}
    for r in existing_rows:
        y = r.get("year")
        sibling.setdefault((y, r.get("std_id")), r)
        if y and y not in source_by_year and (r.get("source") or "").strip():
            source_by_year[y] = r["source"]
        ps = str(r.get("page_start") or "").strip()
        if ps:
            sibling_pages.setdefault((y, r.get("std_id")), set()).add(ps)

    # 페이지 번호는 검수 메모(note)에 적힌 'p.NN' 에서 가져온다. 최신 레코드 note 엔
    # 없고 과거 레코드(원문 대조 시점)에만 있을 수 있어 '전체 레코드'를 스캔한다.
    all_records = load_corrections(path)
    page_by_key: dict[tuple, str] = {}
    for rec in all_records:
        key = (rec.get("year"), rec.get("std_id"))
        if key not in page_by_key:
            m = _PAGE_RE.search(rec.get("note") or "")
            if m:
                page_by_key[key] = m.group(1)

    rows: list[dict] = []
    for rec in latest_by_key(all_records).values():
        status = rec.get("status")
        if status in (STATUS_FIXED, STATUS_LLM_VERIFIED):
            value = (rec.get("new_value") or "").strip()
        elif status == STATUS_CONFIRMED:
            value = (rec.get("old_value") or "").strip()
        else:
            continue  # skip 은 인덱싱하지 않는다
        if not value:
            continue
        if row_key(rec) in existing_full:
            continue  # 대응 행이 이미 있음 → apply_corrections 가 값을 채운다

        year, std_id, std_resp = row_key(rec)
        sib = sibling.get((year, std_id), {})
        meta = _RESTORED_TABLE_META.get((year, std_id), {})
        std_label = meta.get("std_label") or sib.get("std_label") or (std_id or "").replace("_", " ")
        summary = meta.get("question_summary") or sib.get("question_summary") or std_label
        # 페이지: 검수 메모의 p.NN 이 최우선. 없으면 그룹이 '단일 페이지'일 때만 그 페이지를
        # 상속하고, 여러 페이지에 걸쳐 있으면 빈칸으로 둔다(잘못된 출처 인용을 지어내지 않음).
        grp_pages = sibling_pages.get((year, std_id), set())
        inferred_page = next(iter(grp_pages)) if len(grp_pages) == 1 else ""
        page = page_by_key.get((year, std_id)) or inferred_page
        rows.append({
            "year": year,
            "std_id": std_id,
            "std_label": std_label,
            # 비슷한 다른 표와 구분되도록 명시적 요약을 쓴다(없으면 라벨로 대체).
            # '검수 복원'이라는 출처는 warning(메타)에만 남긴다.
            "question_summary": summary,
            "source": sib.get("source") or source_by_year.get(year, ""),
            "page_start": page,
            "page_end": page,
            "std_response_label": std_resp,
            "value": value,
            "unit": sib.get("unit") or "%",
            "warning": "검수 복원(표 추출 누락 → 사람 확정값)",
        })
    return rows


def apply_corrections(rows: list[dict], path: Path = CORRECTIONS_PATH) -> tuple[list[dict], int]:
    """ (5.4 재정제용) 보정값을 데이터 행들에 덮어쓴다.

    - status='fixed'/'llm_verified' 인 최신 보정만 반영한다(confirmed/skip 은 값 변경 없음).
    - 원본 rows 는 건드리지 않고 '복사본'을 고쳐서 돌려준다.
    - 반환: (보정 적용된 새 rows, 실제로 값을 바꾼 건수)
    """
    latest = latest_by_key(load_corrections(path))
    # 값을 실제로 바꾸는 보정(fixed·llm_verified)만 남긴다. 키는 (행 식별키 + field).
    fixed = {k: rec for k, rec in latest.items()
             if rec.get("status") in (STATUS_FIXED, STATUS_LLM_VERIFIED)}
    if not fixed:
        return [dict(r) for r in rows], 0

    applied = 0
    new_rows: list[dict] = []
    for r in rows:
        r = dict(r)  # 원본 보존을 위해 복사본을 고친다
        base_key = row_key(r)
        # 이 행에 걸리는 보정을 컬럼(field)별로 찾아 덮어쓴다.
        for rec in fixed.values():
            field = rec.get("field", "value")
            if base_key + (field,) == _record_key(rec):
                r[field] = rec.get("new_value", r.get(field, ""))
                applied += 1
        new_rows.append(r)
    return new_rows, applied
