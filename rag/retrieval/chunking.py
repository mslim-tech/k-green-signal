# rag/retrieval/chunking.py
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
#   uv run python -m rag.retrieval.chunking        # outputs/chunks.jsonl 생성 + 요약 출력
# -----------------------------------------------------------------------------

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from rag.curate import corrections
from rag.curate import methodology
from rag.curate import external_context
from rag.curate import implications
from rag.transform import std_aliases
try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENC = None


from rag.core.paths import OUTPUT_DIR
# dedup(4.2) 결과를 우선 입력으로 (없으면 clean). flagged 도 가능하지만 값은 동일.
_DEDUP = OUTPUT_DIR / "standardized_long.dedup.csv"
_CLEAN = OUTPUT_DIR / "standardized_long.clean.csv"


def source_csv():
    """ 입력 CSV 를 '호출 시점'에 고른다(dedup 우선, 없으면 clean).
        임포트 시점에 고정하면, 첫 세션에서 인제스트로 dedup.csv 가 새로 생겨도
        게이트·인덱싱·대시보드가 프로세스 재시작 전까지 pre-dedup 데이터를 계속 읽는다. """
    return _DEDUP if _DEDUP.exists() else _CLEAN


# (하위호환 스냅샷 — 로직에는 source_csv() 를 쓴다. 임포트 시점 값이라 신선하지 않을 수 있음)
SOURCE_CSV = _DEDUP if _DEDUP.exists() else _CLEAN
CHUNKS_JSONL = OUTPUT_DIR / "chunks.jsonl"

PARSER_TYPE = "standardized_long"   # 이 청크가 어디서 왔는지(메타)


def count_tokens(text: str) -> int:
    """ 청크의 토큰 수(대략). tiktoken 없으면 공백 기준 근사. """
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, len(text.split()))


def load_rows() -> list[dict]:
    src = source_csv()
    if not src.exists():
        raise RuntimeError(
            f"{src} 가 없습니다. 먼저 3~4단계 파이프라인을 실행하세요."
        )
    with open(src, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    # 사람이 확정한 보정값을 먼저 반영(인덱스엔 확정 사실만)
    rows, _ = corrections.apply_corrections(rows)
    # 검수에서 '제외(skip)'로 표시한 행은 인덱싱하지 않는다
    # (근거 없어 값을 확정할 수 없는 행 — 지어내지 않고 빼는 것이 원칙).
    latest = corrections.latest_by_key()
    skipped = {k[:3] for k, rec in latest.items() if rec.get("status") == corrections.STATUS_SKIP}
    if skipped:
        rows = [r for r in rows if corrections.row_key(r) not in skipped]
    # 추출이 깨져 소스에서 사라졌지만 사람이 PDF 대조로 확정한 표(예: 2023 표 3-60
    # '친환경제품 확대 희망')를 복원해 인덱스에 포함한다(확정값만, 지어내지 않음).
    rows += corrections.confirmed_only_rows(rows)
    # 사람이 확정한 '같은 문항' 별칭으로 연도 간 std_id/라벨을 통일(시계열 연결).
    rows = std_aliases.apply_aliases(rows)
    # 보고서가 명시한 '문항명 변경'을 옛 연도로 이어붙인다(예: 녹색제품 인지도 '19~22는
    # 환경표지 마크 인지도). 과거 문항의 실제값을 계승하며 지어내지 않는다.
    rows = std_aliases.backfill_series(rows)
    # 옛 연도에 없는 집계(예: 인지도)를 명시 정의대로 구성 보기 합으로 도출(시대 연결).
    rows = std_aliases.derive_aggregates(rows)
    return rows


def build_chunks(rows: list[dict]) -> list[dict]:
    """ (year, std_id) 그룹마다 청크 1개. 텍스트 + 출처 메타. """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("year"), r.get("std_id"))].append(r)

    chunks: list[dict] = []
    for (year, std_id), members in groups.items():
        # std_id 가 비면(미매핑) None 이 될 수 있다. Chroma 메타는 None 을 거부해
        # 인덱스 빌드 전체가 실패하므로 문자열로 강제한다(빈 문자열 허용).
        std_id = std_id or ""
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

        if not lines:
            continue   # 값 있는 응답이 하나도 없는 문항은 색인하지 않는다(빈 청크 제외).

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


def build_knowledge_chunks() -> list[dict]:
    """ 큐레이션된 '방법론 주석'을 지식청크로 만든다(parser_type='methodology').
        정형 사실 청크와 섞이지 않게 별도 타입으로 인덱싱한다 — RAG 가 척도 변경 등
        '비교 유의'를 근거로 쓸 수 있게 한다("추측"이 아니라 사람 확정 지식이라 인덱싱). """
    chunks: list[dict] = []
    for i, n in enumerate(methodology.load_notes()):
        std_id = (n.get("std_id") or "").strip()
        std_label = n.get("std_label") or std_id
        note = (n.get("note") or "").strip()
        evidence = (n.get("evidence") or "").strip()
        if not note:
            continue
        text = f"[방법론 주석] {std_label}\n{note}"
        if evidence:
            text += f"\n근거: {evidence}"
        chunk_id = f"methodology__{std_id or i}"
        chunks.append({
            "id": chunk_id,
            "text": text,
            "metadata": {
                "chunk_id": chunk_id,
                "year": "",
                "std_id": std_id,
                "std_label": std_label,
                "source": "방법론 주석(큐레이션)",
                "page": "",
                "parser_type": "methodology",
                "token_count": count_tokens(text),
                "warning": "",
                "n_responses": 0,
            },
        })
    return chunks


def build_external_context_chunks() -> list[dict]:
    """ 큐레이션된 '외부 맥락'(그해 뉴스·사회적 사건)을 지식청크로(parser_type='external_context').
        RAG(특히 advise)가 데이터 변화를 그해 사건과 대조해 '상황 적응형 해석'을 하게 한다
        — 상관·맥락일 뿐 인과 단정 아님(프롬프트가 강제). 정형 사실 청크와 섞이지 않는다. """
    chunks: list[dict] = []
    for i, e in enumerate(external_context.load_events()):
        title = (e.get("title") or "").strip()
        year = str(e.get("year") or "").strip()
        source = (e.get("source") or "").strip()
        if not title:
            continue
        match = ", ".join(e.get("match", []))
        text = f"[외부 맥락 {year}] {title}"
        if match:
            text += f"\n관련 주제: {match}"
        chunk_id = f"external_context__{year}_{i}"
        chunks.append({
            "id": chunk_id,
            "text": text,
            "metadata": {
                "chunk_id": chunk_id,
                "year": year,
                "std_id": "",
                "std_label": title[:60],
                "source": source or "외부 맥락(큐레이션)",
                "page": "",
                "parser_type": "external_context",
                "token_count": count_tokens(text),
                "warning": "",
                "n_responses": 0,
                "url": (e.get("url") or "").strip(),
            },
        })
    return chunks


def build_implication_chunks() -> list[dict]:
    """ 큐레이션된 '보고서 시사점'(요약·시사점 절의 정성적 결론)을 지식청크로(parser_type='implication').
        RAG(특히 advise)가 정량 수치 나열을 넘어 '당시 연구원의 정책적 결론'을 출처와 함께
        인용하게 한다. 정형 사실 청크와 섞이지 않는다. 내용은 실제 보고서에서 사람이 확정해
        curation/implications.json 에 넣는다(빈 목록이면 청크 0 — 파이프라인 무영향). """
    chunks: list[dict] = []
    for i, e in enumerate(implications.load_implications()):
        implication = (e.get("implication") or "").strip()
        year = str(e.get("year") or "").strip()
        if not implication:
            continue
        std_id = (e.get("std_id") or "").strip()
        related = (e.get("related_metric") or "").strip()
        match = ", ".join(e.get("match", []))
        source = (e.get("source") or "").strip()
        text = f"[보고서 시사점 {year}] {implication}"
        if related:
            text += f"\n관련 수치: {related}"
        if match:
            text += f"\n관련 주제: {match}"
        chunk_id = f"implication__{year}_{i}"
        chunks.append({
            "id": chunk_id,
            "text": text,
            "metadata": {
                "chunk_id": chunk_id,
                "year": year,
                "std_id": std_id,
                "std_label": (e.get("std_label") or implication[:60]),
                "source": source or "보고서 시사점(큐레이션)",
                "page": str(e.get("page") or "").strip(),
                "parser_type": "implication",
                "token_count": count_tokens(text),
                "warning": "",
                "n_responses": 0,
            },
        })
    return chunks


def build_all_chunks(rows: list[dict]) -> list[dict]:
    """ 인덱스에 실제로 넣을 전체 청크 = 정형 사실 청크 + 방법론 지식청크 + 외부 맥락 지식청크
        + 보고서 시사점 지식청크.
        (게이트 validate 는 사실 청크만 build_chunks 로 검사하므로 영향 없음.) """
    return (build_chunks(rows) + build_knowledge_chunks()
            + build_external_context_chunks() + build_implication_chunks())


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
    chunks = build_all_chunks(load_rows())
    save_chunks(chunks)
    return chunks


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    rows = load_rows()
    # build_all_chunks 와 동일 구성(개별 카운트는 요약 출력용). 여기서 한 종류라도 빠지면
    # 그 지식청크가 인덱스에서 통째로 누락되므로(index 는 이 chunks.jsonl 을 읽음) 반드시 일치시킨다.
    fact_chunks = build_chunks(rows)
    know_chunks = build_knowledge_chunks()
    ctx_chunks = build_external_context_chunks()
    impl_chunks = build_implication_chunks()
    chunks = fact_chunks + know_chunks + ctx_chunks + impl_chunks
    path = save_chunks(chunks)

    n_tok = sum(c["metadata"]["token_count"] for c in chunks)
    empty = sum(1 for c in fact_chunks if c["metadata"]["n_responses"] == 0)
    print("\n" + "=" * 60)
    print(f"청킹 완료 — {len(chunks)} 청크 (사실 {len(fact_chunks)} + 방법론 지식 {len(know_chunks)} "
          f"+ 외부 맥락 {len(ctx_chunks)} + 보고서 시사점 {len(impl_chunks)}, "
          f"{source_csv().name} 기준, corrections 반영)")
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
