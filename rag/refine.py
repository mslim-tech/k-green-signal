# rag/refine.py
# -----------------------------------------------------------------------------
# 4단계 4.1: 응답 라벨 표준화
#
# 이 파일의 역할:
#   - 3단계(standardize.py)는 "문항"을 표준 ID(std_id)로 묶었다.
#     그런데 같은 문항 안에서도 "응답 라벨"이 연도마다 다르게 적혀 있다.
#       예) 2025 "SNS, 블로그 등 인터넷"  vs  2024 "SNS, 카페 및 정보 블로그 등 인터넷"
#           "모름"  vs  "알고 있지 않음"
#   - 이 라벨들을 문항(std_id)별로 모아 LLM 에게 "같은 뜻끼리 묶고 대표 라벨을 정하라"고
#     시킨다. 그 결과를 사전으로 저장하고, CSV 에 std_response_label 컬럼을 새로 붙인다.
#
#   왜 문항(std_id) 단위로 묶나:
#     - 라벨의 의미는 문항 맥락 안에서만 같다. (예 "기타"는 어느 문항에나 있지만
#       서로 다른 문항의 "기타"를 묶으면 안 된다.)
#     - 문항별로 나누면 한 번에 다루는 라벨 수가 적어 LLM 정확도도 올라간다.
#
#   산출(원본은 절대 건드리지 않는다):
#     1) outputs/response_label_map.json      - {std_id: {원본라벨: 대표라벨}} 사전(검수용)
#     2) outputs/standardized_long.clean.csv   - 원본 + std_response_label 컬럼 추가본
#
# 보안: API Key 는 .env 의 OPENAI_API_KEY 에서만 읽는다.
#
# 실행 방법(3단계 표준화가 끝난 뒤):
#   uv run python rag/refine.py
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import json
import sys
from collections import OrderedDict
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
SOURCE_CSV = OUTPUT_DIR / "standardized_long.csv"        # 3단계 산출 (입력, 보존)
MAP_PATH = OUTPUT_DIR / "response_label_map.json"        # 4.1.3 산출
CLEAN_CSV = OUTPUT_DIR / "standardized_long.clean.csv"   # 4.1.4 산출


# LLM 이 문항 하나마다 돌려줄 형식 (Structured Outputs, strict)
#   groups: 같은 뜻으로 묶인 라벨 묶음들. 각 묶음은 대표(canonical)와 그 안의 원본 라벨들.
LABEL_GROUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["groups"],
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["canonical", "members"],
                "properties": {
                    # 이 묶음의 대표 라벨 (members 중 가장 명확/완전한 표현을 고른다)
                    "canonical": {"type": "string"},
                    # 이 대표 라벨로 묶일 원본 라벨들 (canonical 자신도 포함)
                    "members": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


SYSTEM_PROMPT = (
    "너는 같은 설문 문항에서 여러 해에 걸쳐 다르게 적힌 '응답 라벨'을 의미 단위로 묶는 도구다.\n"
    "한 문항에 등장한 응답 라벨 목록을 준다. 뜻이 같은 라벨끼리 묶고, 각 묶음의 대표 라벨을 정해라.\n\n"
    "규칙:\n"
    "- 표기만 다르고 의미가 같으면 묶어라. 예) 'SNS, 블로그 등 인터넷' = 'SNS, 카페 및 정보 블로그 등 인터넷',\n"
    "  'TV, 유튜브 등 영상매체' = 'TV, 유튜브 등 영상매체를 통해', '알고 있다' = '인지하고 있음'.\n"
    "- ★가장 중요★ 긍정/부정 등 '반대 뜻' 응답은 절대 묶지 마라. 이건 데이터를 망치는 치명적 실수다.\n"
    "  절대 묶으면 안 되는 예) '알고 있음' ↔ '모르고 있음'(='모른다'), '예' ↔ '아니오',\n"
    "  '인지' ↔ '비인지', '관심 있음' ↔ '관심 없음', '만족' ↔ '불만족'.\n"
    "  (이런 쌍은 표현이 비슷해 보여도 정반대 응답이므로 각각 다른 묶음이어야 한다.)\n"
    "- 같은 뜻의 부정 표현끼리는 묶어도 된다. 예) '모름' = '알고 있지 않음' = '모르고 있음'.\n"
    "- 의미가 다르면 절대 묶지 마라. 예) '대기오염' 과 '지구온난화' 는 다른 항목이다.\n"
    "- ★품목/제품종류/항목 보기는 '같은 것을 다르게 적은 경우'만 묶어라. 비슷한 범주라도 서로 다른\n"
    "  보기면 합치지 마라. 합쳐도 되는 예) '가구'='가구제품', '가전제품'='전자제품'(완전 동의).\n"
    "  절대 합치면 안 되는 예) '전기자동차'·'자동차'·'매연 없는 자동차'(서로 다른 보기),\n"
    "  '비닐'·'빨대'·'플라스틱 대체품'·'자연분해 가능한 제품', '저탄소 제품'·'1등급 가전'·'전자파 없는 가전'.\n"
    "  (개방형 응답은 세부 보기가 많다. 표기 차이가 아니라 다른 대상이면 각자 따로 둬라.)\n"
    "- 대표 라벨(canonical)은 묶인 원본들 중 가장 명확하고 완전한 표현을 그대로 고른다(새로 지어내지 말 것).\n"
    "- 묶이지 않는 라벨은 자기 혼자만 든 묶음으로 만들어라(members 길이 1, canonical=자기 자신).\n"
    "- 입력에 준 모든 라벨이 정확히 한 묶음에만 들어가야 한다. 빠뜨리거나 중복시키지 마라."
)


# -----------------------------------------------------------------------------
# 1) 4.1.1 — std_id 별로 등장한 응답 라벨 전부 수집
# -----------------------------------------------------------------------------
def load_rows() -> list[dict]:
    """ 3단계 산출 CSV 를 읽어 행 목록으로 돌려준다. """
    if not SOURCE_CSV.exists():
        raise RuntimeError(
            f"{SOURCE_CSV} 가 없습니다. 먼저 rag/standardize.py 로 표준화를 실행하세요."
        )
    # utf-8-sig: standardize.py 가 BOM 포함으로 저장하므로 그대로 읽는다.
    with open(SOURCE_CSV, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def collect_labels_by_question(rows: list[dict]) -> "OrderedDict[str, list[str]]":
    """
    std_id 별로 등장한 고유 응답 라벨을 모은다(등장 순서 유지).
    빈 라벨은 건너뛴다.
    """
    by_q: "OrderedDict[str, list[str]]" = OrderedDict()
    for r in rows:
        sid = r.get("std_id") or ""
        label = (r.get("response_label") or "").strip()
        if not sid or not label:
            continue
        seen = by_q.setdefault(sid, [])
        if label not in seen:
            seen.append(label)
    return by_q


# -----------------------------------------------------------------------------
# 2) 4.1.2 — LLM 으로 동의 라벨 묶기 (문항 단위 호출)
# -----------------------------------------------------------------------------
def _call_group_labels(client, std_id: str, labels: list[str], retries: int = 2) -> dict:
    """ 한 문항의 라벨 목록을 LLM 에 주고 {groups:[...]} 를 받는다. 실패하면 빈 결과. """
    user_prompt = (
        f"[문항 ID] {std_id}\n\n"
        "[이 문항에 등장한 응답 라벨]\n"
        + json.dumps(labels, ensure_ascii=False, indent=2)
    )
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
                        "name": "response_label_grouping",
                        "strict": True,
                        "schema": LABEL_GROUP_SCHEMA,
                    },
                },
            )
            return json.loads(response.choices[0].message.content)
        except Exception as error:
            last_error = error
    print(f"  ⚠️ '{std_id}' 라벨 묶기 실패: {last_error}")
    return {"groups": []}


def build_label_map(client, by_q: "OrderedDict[str, list[str]]") -> dict[str, dict[str, str]]:
    """
    문항별로 {원본라벨: 대표라벨} 사전을 만든다.
    LLM 이 빠뜨리거나 모르는 라벨은 '자기 자신'을 대표로 두어(항등) 데이터 손실을 막는다.
    """
    label_map: dict[str, dict[str, str]] = {}
    total = len(by_q)
    for i, (sid, labels) in enumerate(by_q.items(), start=1):
        # 라벨이 1개뿐이면 묶을 게 없으니 LLM 호출을 아낀다.
        if len(labels) <= 1:
            label_map[sid] = {lab: lab for lab in labels}
            print(f"  [{i}/{total}] {sid}: 라벨 {len(labels)}개 (호출 생략)")
            continue

        result = _call_group_labels(client, sid, labels)
        mapping: dict[str, str] = {}
        for group in result.get("groups", []):
            canonical = (group.get("canonical") or "").strip()
            members = group.get("members") or []
            if not canonical:
                continue
            for m in members:
                m = (m or "").strip()
                if m:
                    mapping[m] = canonical

        # 안전망: LLM 이 누락한 원본 라벨은 자기 자신을 대표로(항등 매핑).
        for lab in labels:
            mapping.setdefault(lab, lab)

        label_map[sid] = mapping
        groups_n = len({v for v in mapping.values()})
        print(f"  [{i}/{total}] {sid}: 라벨 {len(labels)}개 → 대표 {groups_n}개")

    return label_map


# -----------------------------------------------------------------------------
# 3) 4.1.3 / 4.1.4 — 사전 저장 + clean CSV 저장
# -----------------------------------------------------------------------------
def save_label_map(label_map: dict[str, dict[str, str]]) -> Path:
    """ {std_id: {원본라벨: 대표라벨}} 사전을 검수용 JSON 으로 저장한다. """
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(label_map, f, ensure_ascii=False, indent=2)
    return MAP_PATH


def save_clean_csv(rows: list[dict], label_map: dict[str, dict[str, str]]) -> Path:
    """
    원본 컬럼 + std_response_label 을 더해 clean CSV 로 저장한다.
    원본 response_label 은 그대로 보존한다.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    # 원본 컬럼 순서 유지 + response_label 바로 뒤에 std_response_label 삽입
    src_cols = list(rows[0].keys()) if rows else []
    out_cols: list[str] = []
    for col in src_cols:
        out_cols.append(col)
        if col == "response_label":
            out_cols.append("std_response_label")
    if "std_response_label" not in out_cols:  # 혹시 원본에 response_label 이 없으면 끝에 추가
        out_cols.append("std_response_label")

    with open(CLEAN_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_cols)
        writer.writeheader()
        for r in rows:
            sid = r.get("std_id") or ""
            label = (r.get("response_label") or "").strip()
            mapping = label_map.get(sid, {})
            # 라벨이 비어 있으면 std 라벨도 빈칸으로 둔다.
            std_label = mapping.get(label, label) if label else ""
            writer.writerow({**r, "std_response_label": std_label})
    return CLEAN_CSV


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    rows = load_rows()
    by_q = collect_labels_by_question(rows)
    total_labels = sum(len(v) for v in by_q.values())
    print(f"\n응답 라벨 표준화: 문항 {len(by_q)}개 · 고유 라벨 {total_labels}개 ({MODEL_NAME})\n")

    client = get_client()
    label_map = build_label_map(client, by_q)

    map_path = save_label_map(label_map)
    csv_path = save_clean_csv(rows, label_map)

    # 요약: 실제로 합쳐진 라벨 수(원본 - 대표) = 표준화로 줄어든 라벨 개수
    merged = 0
    for mapping in label_map.values():
        merged += len(mapping) - len({v for v in mapping.values()})

    print("\n" + "=" * 60)
    print(f"라벨 표준화 완료 — 합쳐진 라벨 {merged}개 (서로 다른 표기를 같은 대표로 통합)")
    print(f"💾 라벨 사전  : {map_path}")
    print(f"💾 clean CSV : {csv_path}  (원본 {SOURCE_CSV.name} 은 그대로 보존)")
    print("=" * 60)


if __name__ == "__main__":
    main()
