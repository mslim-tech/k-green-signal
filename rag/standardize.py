# rag/standardize.py
# -----------------------------------------------------------------------------
# 3단계: LLM 문항 표준화 + 연도 통합 데이터셋 만들기
#
# 이 파일의 역할:
#   - 2단계(extract.py)가 만든 연도별 추출 결과(outputs/*.extracted.jsonl)를 모아,
#     해마다 표현이 다른 문항들을 "표준 문항 ID(std_id)"로 묶는다.
#       예) 2024 "노력 정도" / 2025 "노력 정도" / 2023 "실천 정도"  →  같은 std_id
#   - 표준화는 LLM(gpt-4o)에게 맡긴다. "표준 문항 사전"을 배치마다 키워가며,
#     새 문항이 기존 표준에 해당하면 그 std_id 에 매핑하고, 없으면 새로 만든다.
#     (사전을 계속 넘겨주므로 연도가 달라도 같은 개념은 같은 std_id 로 모인다.)
#
#   최종 산출(사용자 선택: Long-format):
#     1) outputs/question_dictionary.json  - 표준 문항 사전(어떤 연도들이 묶였는지 포함)
#     2) outputs/standardized_long.csv      - 연도 통합 tidy 데이터셋
#        (한 행 = 한 연도의 한 표준문항의 한 응답항목 수치)
#
# 보안: API Key 는 .env 의 OPENAI_API_KEY 에서만 읽는다.
#
# 실행 방법(2단계 추출이 끝난 뒤):
#   uv run python rag/standardize.py
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 같은 폴더/루트 어디서 실행해도 import 되도록
try:
    from rag.extract import get_client
    from rag.config import STANDARDIZE_MODEL as MODEL_NAME
except ImportError:
    from extract import get_client
    from config import STANDARDIZE_MODEL as MODEL_NAME


try:
    from rag.paths import OUTPUT_DIR
except ImportError:
    from paths import OUTPUT_DIR
BATCH_SIZE = 40  # 한 번의 LLM 호출에 넘기는 문항 수


# LLM 이 배치마다 돌려줄 형식 (Structured Outputs, strict)
STANDARDIZE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["assignments", "new_entries"],
    "properties": {
        # 이번 배치의 각 문항을 어떤 std_id 로 매핑했는지
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "std_id"],
                "properties": {
                    "id": {"type": "integer"},       # 입력 문항 번호
                    "std_id": {"type": "string"},    # 매핑된 표준 문항 ID
                },
            },
        },
        # 사전에 없어서 새로 만든 표준 문항들
        "new_entries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["std_id", "std_label", "category"],
                "properties": {
                    "std_id": {"type": "string"},      # 표준 ID 슬러그 (예: "친환경_관심도")
                    "std_label": {"type": "string"},   # 표준 문항명(사람용)
                    "category": {"type": "string"},    # 대분류 테마
                },
            },
        },
    },
}


SYSTEM_PROMPT = (
    "너는 여러 해에 걸친 설문조사 문항을 '표준 문항'으로 통합하는 도구다.\n"
    "지금까지 만든 '표준 문항 사전'과, 새로 분류할 문항 목록을 준다.\n"
    "각 문항에 대해:\n"
    "- 의미(묻는 핵심)가 같은 표준 문항이 사전에 있으면 그 std_id 로 매핑한다.\n"
    "- 사전에 없으면 new_entries 에 새 표준 문항을 추가하고, 그 std_id 로 매핑한다.\n\n"
    "규칙:\n"
    "- 연도마다 표현이 달라도(예: '노력 정도' vs '실천 정도') 묻는 핵심이 같으면 같은 std_id 로 묶어라.\n"
    "- std_id 는 한글/영문/숫자/밑줄로 된 짧은 슬러그(예: '친환경_관심도').\n"
    "- 이미 사전에 있는 std_id 는 절대 다른 의미로 재사용하지 마라.\n"
    "- 이번 배치의 모든 문항 id 를 빠짐없이 assignments 에 넣어라.\n"
    "- 같은 배치 안에서 새로 만든 std_id 는 new_entries 에도 반드시 넣어라."
)


@dataclass
class Instance:
    """ 표준화 대상이 되는 문항 1건 (추출 레코드에서 표준화에 필요한 부분만). """
    id: int
    record: dict  # 원본 추출 레코드 전체 (나중에 CSV 로 펼칠 때 사용)

    @property
    def year(self):
        return self.record.get("year")

    @property
    def summary(self):
        return self.record.get("question_summary") or ""

    @property
    def section(self):
        return self.record.get("section") or ""

    @property
    def subsection(self):
        return self.record.get("subsection") or ""


# -----------------------------------------------------------------------------
# 1) 2단계 추출 결과 불러오기
# -----------------------------------------------------------------------------
def load_records() -> list[dict]:
    """ outputs/*.extracted.jsonl 을 모두 읽어 레코드 목록으로 돌려준다. """
    files = sorted(OUTPUT_DIR.glob("*.extracted.jsonl"))
    if not files:
        raise RuntimeError(
            "outputs/ 에 추출 결과(*.extracted.jsonl)가 없습니다. "
            "먼저 rag/extract.py 로 전체 추출을 실행하세요."
        )
    records: list[dict] = []
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


# -----------------------------------------------------------------------------
# 2) LLM 으로 표준 문항 사전 만들기 (배치 누적)
# -----------------------------------------------------------------------------
def _batch_prompt(dictionary: dict[str, dict], batch: list[Instance]) -> str:
    """ 현재 사전 + 이번 배치 문항 목록을 LLM 입력 텍스트로 만든다. """
    dict_view = [
        {"std_id": e["std_id"], "std_label": e["std_label"], "category": e["category"]}
        for e in dictionary.values()
    ]
    questions = [
        {
            "id": inst.id,
            "year": inst.year,
            "section": inst.section,
            "subsection": inst.subsection,
            "question_summary": inst.summary,
        }
        for inst in batch
    ]
    return (
        "[현재 표준 문항 사전]\n"
        + json.dumps(dict_view, ensure_ascii=False, indent=2)
        + "\n\n[분류할 문항 목록]\n"
        + json.dumps(questions, ensure_ascii=False, indent=2)
    )


def _call_standardize(client, user_prompt: str, retries: int = 2) -> dict:
    """ LLM 호출 → {assignments, new_entries}. 실패하면 빈 결과. """
    last_error = None
    for _ in range(retries):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "question_standardization",
                        "strict": True,
                        "schema": STANDARDIZE_SCHEMA,
                    },
                },
            )
            return json.loads(response.choices[0].message.content)
        except Exception as error:
            last_error = error
    print(f"  ⚠️ 표준화 배치 실패: {last_error}")
    return {"assignments": [], "new_entries": []}


def build_dictionary(client, instances: list[Instance]) -> tuple[dict[str, dict], dict[int, str]]:
    """
    모든 문항을 배치로 나눠 표준화한다.
    반환:
      dictionary : {std_id: {std_id, std_label, category}}
      assignment : {instance_id: std_id}
    """
    dictionary: dict[str, dict] = {}
    assignment: dict[int, str] = {}

    total_batches = (len(instances) + BATCH_SIZE - 1) // BATCH_SIZE
    for bi in range(total_batches):
        batch = instances[bi * BATCH_SIZE : (bi + 1) * BATCH_SIZE]
        result = _call_standardize(client, _batch_prompt(dictionary, batch))

        # 새 표준 문항을 사전에 추가 (이미 있으면 무시)
        for entry in result.get("new_entries", []):
            sid = entry.get("std_id")
            if sid and sid not in dictionary:
                dictionary[sid] = {
                    "std_id": sid,
                    "std_label": entry.get("std_label", sid),
                    "category": entry.get("category", ""),
                }

        # 각 문항의 std_id 기록
        for a in result.get("assignments", []):
            iid, sid = a.get("id"), a.get("std_id")
            if iid is None or not sid:
                continue
            assignment[iid] = sid
            # LLM 이 new_entries 에 안 넣고 매핑만 한 std_id 도 사전에 보강
            if sid not in dictionary:
                dictionary[sid] = {"std_id": sid, "std_label": sid, "category": ""}

        print(f"  배치 {bi + 1}/{total_batches} 완료 — 누적 표준문항 {len(dictionary)}개")

    return dictionary, assignment


# -----------------------------------------------------------------------------
# 3) 결과 저장: 문항 사전 + 연도 통합 long CSV
# -----------------------------------------------------------------------------
LONG_CSV_COLUMNS = [
    "std_id", "std_label", "category",
    "year", "response_label", "value", "unit", "base_n", "multi_response",
    "question_summary", "section", "subsection",
    "prev_year_note", "source", "page_start", "page_end",
    "extraction_confidence", "warning", "figures",
]


def save_dictionary(dictionary: dict[str, dict], instances: list[Instance],
                    assignment: dict[int, str]) -> Path:
    """ 표준 문항 사전을 저장한다. 각 표준문항이 어떤 연도들에 등장했는지도 담는다. """
    # std_id 별로 묶인 연도/원본 정보 모으기
    members: dict[str, list[dict]] = {sid: [] for sid in dictionary}
    for inst in instances:
        sid = assignment.get(inst.id)
        if sid:
            members.setdefault(sid, []).append({
                "year": inst.year,
                "source": inst.record.get("source"),
                "question_summary": inst.summary,
            })

    out = []
    for sid, entry in dictionary.items():
        ms = members.get(sid, [])
        years = sorted({m["year"] for m in ms if m["year"] is not None})
        out.append({
            **entry,
            "years_covered": years,
            "instance_count": len(ms),
            "members": ms,
        })
    # 많이 등장한(여러 해에 걸친) 표준문항이 위로 오도록 정렬
    out.sort(key=lambda e: (-len(e["years_covered"]), -e["instance_count"]))

    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / "question_dictionary.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return path


def save_long_csv(dictionary: dict[str, dict], instances: list[Instance],
                  assignment: dict[int, str]) -> Path:
    """ 연도 통합 tidy(long) 데이터셋을 CSV 로 저장한다. """
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / "standardized_long.csv"
    # utf-8-sig: 엑셀에서 한글이 깨지지 않도록 BOM 포함
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LONG_CSV_COLUMNS)
        writer.writeheader()
        for inst in instances:
            sid = assignment.get(inst.id, "")
            entry = dictionary.get(sid, {})
            rec = inst.record
            base_row = {
                "std_id": sid,
                "std_label": entry.get("std_label", ""),
                "category": entry.get("category", ""),
                "year": rec.get("year"),
                "unit": rec.get("unit"),
                "base_n": rec.get("base_n"),
                "multi_response": rec.get("multi_response"),
                "question_summary": rec.get("question_summary"),
                "section": rec.get("section"),
                "subsection": rec.get("subsection"),
                "prev_year_note": rec.get("prev_year_note"),
                "source": rec.get("source"),
                "page_start": rec.get("page_start"),
                "page_end": rec.get("page_end"),
                "extraction_confidence": rec.get("extraction_confidence"),
                "warning": rec.get("warning"),
                "figures": " / ".join(rec.get("figures", [])),
            }
            items = rec.get("response_items") or []
            if not items:
                # 응답항목이 없으면 한 줄만 (수치 비움)
                writer.writerow({**base_row, "response_label": "", "value": ""})
            else:
                for it in items:
                    writer.writerow({
                        **base_row,
                        "response_label": it.get("label", ""),
                        "value": it.get("value"),
                    })
    return path


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    records = load_records()
    instances = [Instance(id=i, record=r) for i, r in enumerate(records)]
    print(f"\n추출 레코드 {len(instances)}건을 표준화합니다 ({MODEL_NAME})...\n")

    client = get_client()
    dictionary, assignment = build_dictionary(client, instances)

    # 매핑 누락 확인
    unmapped = [inst.id for inst in instances if inst.id not in assignment]
    if unmapped:
        print(f"\n⚠️ 표준 ID 미매핑 {len(unmapped)}건 (CSV 에는 std_id 빈칸으로 들어감)")

    dict_path = save_dictionary(dictionary, instances, assignment)
    csv_path = save_long_csv(dictionary, instances, assignment)

    # 요약: 여러 해에 걸쳐 묶인 표준문항(= 연도 비교가 가능한 핵심 문항)
    multi_year = 0
    for sid in dictionary:
        years = {inst.year for inst in instances if assignment.get(inst.id) == sid}
        if len([y for y in years if y]) >= 2:
            multi_year += 1

    print("\n" + "=" * 60)
    print(f"표준 문항 {len(dictionary)}개  (이 중 2개년 이상 묶인 문항 {multi_year}개)")
    print(f"💾 문항 사전 : {dict_path}")
    print(f"💾 통합 CSV  : {csv_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
