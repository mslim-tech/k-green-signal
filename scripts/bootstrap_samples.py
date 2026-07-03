# scripts/bootstrap_samples.py
# -----------------------------------------------------------------------------
# 클론 즉시 재현용 부트스트랩 — 저장소에 커밋된 레퍼런스(samples/)를 '작업 폴더'로 복사한다.
#
#   samples/data/    → data/      (공식 홈페이지 공개 PDF 원본)
#   samples/outputs/ → outputs/   (정형 CSV·청크·corrections·Chroma 인덱스)
#
# 왜 복사인가: data/·outputs/ 는 .gitignore 로 추적하지 않는다(각자 데이터 → git 충돌 0).
# 레퍼런스만 samples/ 에 커밋해 두고, 이 스크립트로 작업 폴더에 펼친다. 그러면 클론 후
# 키 없이도 신호등 대시보드가 바로 동작한다(검색·답변 생성은 질문 임베딩부터 OPENAI_API_KEY 필요).
#
# 안전장치: 작업 폴더에 이미 파일이 있으면 덮어쓰지 않고 건너뛴다(자기 작업 보호).
#           전부 레퍼런스로 초기화하려면  --force  를 준다.
#
# 사용:
#   uv run python scripts/bootstrap_samples.py            # 비어 있을 때만 채움(안전)
#   uv run python scripts/bootstrap_samples.py --force    # 작업 폴더를 레퍼런스로 덮어씀
# -----------------------------------------------------------------------------

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAIRS = [
    (ROOT / "samples" / "data", ROOT / "data"),
    (ROOT / "samples" / "outputs", ROOT / "outputs"),
]


def _has_files(d: Path) -> bool:
    """ 디렉터리에 (숨김 아닌) 파일이 하나라도 있으면 True. """
    return d.exists() and any(p.is_file() for p in d.rglob("*"))


def main() -> None:
    force = "--force" in sys.argv[1:]

    if not (ROOT / "samples").exists():
        print("❌ samples/ 가 없습니다. 저장소를 제대로 클론했는지 확인하세요.")
        raise SystemExit(1)

    for src, dst in PAIRS:
        rel = dst.name
        if not src.exists():
            print(f"⚠️  {src} 없음 — 건너뜀")
            continue
        if _has_files(dst) and not force:
            print(f"⏭️  {rel}/ 에 이미 파일이 있어 건너뜀(자기 작업 보호). "
                  f"레퍼런스로 초기화하려면 --force")
            continue
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for item in src.iterdir():
            target = dst / item.name
            if item.is_dir():
                if target.exists() and force:
                    shutil.rmtree(target)
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
            n += 1
        print(f"✅ {src.relative_to(ROOT)} → {rel}/  ({n}개 항목 복사)")

    print("\n다음 단계:")
    print("  1) (선택) RAG 답변 생성을 쓰려면  cp .env.example .env  후 OPENAI_API_KEY 채우기")
    print("  2) uv run streamlit run app.py     # 신호등 대시보드는 키 없이도 동작")


if __name__ == "__main__":
    main()
