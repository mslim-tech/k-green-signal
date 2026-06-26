# rag/corrections.py
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
from datetime import datetime
from pathlib import Path


OUTPUT_DIR = Path("outputs")
CORRECTIONS_PATH = OUTPUT_DIR / "corrections.jsonl"

# 한 행을 가리키는 식별 키를 이루는 컬럼들.
# review_queue 의 중복키와 동일하게 (연도, 표준문항, 표준응답라벨) 로 행을 특정한다.
KEY_FIELDS = ("year", "std_id", "std_response_label")

# 검수 상태 값(허용 목록). UI 와 재정제가 같은 단어를 쓰도록 한 곳에 모은다.
STATUS_FIXED = "fixed"          # 값을 고쳤다 -> 재정제 때 new_value 로 덮어쓴다
STATUS_CONFIRMED = "confirmed"  # 원래 값이 맞다(검수 완료) -> 값은 그대로 둔다
STATUS_SKIP = "skip"            # 판단 보류 -> 아무것도 하지 않는다
VALID_STATUSES = (STATUS_FIXED, STATUS_CONFIRMED, STATUS_SKIP)


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
      status    - 'fixed' / 'confirmed' / 'skip' 중 하나.
      new_value - status='fixed' 일 때 고친 값. (그 외에는 빈 값이어도 됨)
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


def apply_corrections(rows: list[dict], path: Path = CORRECTIONS_PATH) -> tuple[list[dict], int]:
    """ (5.4 재정제용) 보정값을 데이터 행들에 덮어쓴다.

    - status='fixed' 인 최신 보정만 반영한다(confirmed/skip 은 값 변경 없음).
    - 원본 rows 는 건드리지 않고 '복사본'을 고쳐서 돌려준다.
    - 반환: (보정 적용된 새 rows, 실제로 값을 바꾼 건수)
    """
    latest = latest_by_key(load_corrections(path))
    # 값을 실제로 바꾸는 보정(fixed)만 남긴다. 키는 (행 식별키 + field).
    fixed = {k: rec for k, rec in latest.items() if rec.get("status") == STATUS_FIXED}
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
