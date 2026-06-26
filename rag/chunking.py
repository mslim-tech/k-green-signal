# rag/chunking.py
# -----------------------------------------------------------------------------
# 6.1 청킹 (Chunking)
#
# 이 파일의 역할:
#   - 정형 DB(standardized_long.dedup.csv)를 검색·인용하기 좋은 '청크'로 만든다.
#   - 청크 단위 = (year, std_id) 한 문항. 그 문항의 질문 + 전체 응답 분포를
#     사람이 읽는 한 덩어리 텍스트로 만들고, 출처(source/page) 메타를 붙인다.
#   - 검수에서 사람이 확정한 값(corrections.jsonl)을 먼저 반영한 뒤 청킹한다.
#     → 인덱스에는 '확정된 사실'이 들어간다(설계 원칙: 추측은 데이터가 아니다).
#
#   메타데이터(CLAUDE.md 필수 + 확장):
#     source, page, parser_type, chunk_id, token_count, warning,
#     year, std_id, std_label
#
# 실행:
#   uv run python rag/chunking.py        # outputs/chunks.jsonl 생성 + 요약 출력
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

try:
    from rag import corrections
except ImportError:
    import corrections

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENC = None


OUTPUT_DIR = Path("outputs")
# dedup(4.2) 결과를 우선 입력으로 (없으면 clean). flagged 도 가능하지만 값은 동일.
_DEDUP = OUTPUT_DIR / "standardized_long.dedup.csv"
_CLEAN = OUTPUT_DIR / "standardized_long.clean.csv"
SOURCE_CSV = _DEDUP if _DEDUP.exists() else _CLEAN
CHUNKS_JSONL = OUTPUT_DIR / "chunks.jsonl"

PARSER_TYPE = "standardized_long"   # 이 청크가 어디서 왔는지(메타)


def count_tokens(text: str) -> int:
    """ 청크의 토큰 수(대략). tiktoken 없으면 공백 기준 근사. """
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, len(text.split()))


def load_rows() -> list[dict]:
    if not SOURCE_CSV.exists():
        raise RuntimeError(
            f"{SOURCE_CSV} 가 없습니다. 먼저 3~4단계 파이프라인을 실행하세요."
        )
    with open(SOURCE_CSV, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    # 사람이 확정한 보정값을 먼저 반영(인덱스엔 확정 사실만)
    rows, _ = corrections.apply_corrections(rows)
    return rows


def build_chunks(rows: list[dict]) -> list[dict]:
    """ (year, std_id) 그룹마다 청크 1개. 텍스트 + 출처 메타. """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("year"), r.get("std_id"))].append(r)

    chunks: list[dict] = []
    for (year, std_id), members in groups.items():
        head = members[0]
        std_label = head.get("std_label") or std_id
        summary = head.get("question_summary") or ""
        source = head.get("source") or ""

        # 페이지 범위(그룹 전체에서 최소~최대)
        def _ints(key):
            out = []
            for m in members:
                v = (m.get(key) or "").strip()
                if v.isdigit():
                    out.append(int(v))
            return out
        pages = _ints("page_start") + _ints("page_end")
        if pages:
            p0, p1 = min(pages), max(pages)
            page_str = f"{p0}" if p0 == p1 else f"{p0}-{p1}"
        else:
            page_str = ""

        # 응답 분포 줄 (값 있는 것만)
        lines = []
        for m in members:
            label = (m.get("std_response_label") or m.get("response_label") or "").strip()
            value = (m.get("value") or "").strip()
            unit = (m.get("unit") or "").strip()
            if label and value:
                lines.append(f"- {label}: {value}{unit}")

        # 사람이 읽는 청크 본문 (검색 임베딩 + 인용 표시용)
        text = (
            f"[{year}년] {std_label}\n"
            f"질문: {summary}\n"
            f"출처: {source} p.{page_str}\n"
            f"응답(전체 기준):\n" + "\n".join(lines)
        )

        warnings = sorted({(m.get("warning") or "").strip() for m in members if (m.get("warning") or "").strip()})
        chunk_id = f"{year}__{std_id}"
        chunks.append({
            "id": chunk_id,
            "text": text,
            "metadata": {
                "chunk_id": chunk_id,
                "year": str(year),
                "std_id": std_id,
                "std_label": std_label,
                "source": source,
                "page": page_str,
                "parser_type": PARSER_TYPE,
                "token_count": count_tokens(text),
                "warning": " | ".join(warnings),
                "n_responses": len(lines),
            },
        })
    return chunks


def save_chunks(chunks: list[dict]) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(CHUNKS_JSONL, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return CHUNKS_JSONL


def load_chunks() -> list[dict]:
    """ index.py 등에서 재사용: 저장된 청크 읽기. 없으면 새로 생성. """
    if CHUNKS_JSONL.exists():
        with open(CHUNKS_JSONL, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    chunks = build_chunks(load_rows())
    save_chunks(chunks)
    return chunks


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    rows = load_rows()
    chunks = build_chunks(rows)
    path = save_chunks(chunks)

    n_tok = sum(c["metadata"]["token_count"] for c in chunks)
    empty = sum(1 for c in chunks if c["metadata"]["n_responses"] == 0)
    print("\n" + "=" * 60)
    print(f"청킹 완료 — {len(chunks)} 청크 ({SOURCE_CSV.name} 기준, corrections 반영)")
    print(f"  총 토큰(대략): {n_tok:,} | 평균 {n_tok // max(1,len(chunks))}/청크")
    if empty:
        print(f"  ⚠️ 응답 줄이 0인 청크: {empty}개 (값이 다 빈 문항 — 검수 대상)")
    print(f"💾 {path}")
    print("=" * 60)
    # 샘플 1개
    if chunks:
        print("\n[샘플 청크]\n" + chunks[0]["text"][:300])


if __name__ == "__main__":
    main()
