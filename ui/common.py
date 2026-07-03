# ui/common.py
# -----------------------------------------------------------------------------
# 여러 UI 모듈이 공유하는 상수(산출물 파일 경로). app.py 와 ui/review.py 가 함께 쓰므로
# 한 곳에 둬 순환 import 를 피한다.
# -----------------------------------------------------------------------------
from pathlib import Path

# 4단계가 만든 검수 큐 파일 위치
REVIEW_QUEUE_PATH = Path("outputs") / "review_queue.csv"
# 비전 재판독(refill_vision)이 제안한 '검토 후보' 파일 위치(값은 아직 데이터 아님).
VISION_CANDIDATES_PATH = Path("outputs") / "vision_candidates.csv"

# 원본 PDF 디렉터리(업로드·인제스트·상태 카운트가 공유).
DATA_DIR = Path("data")


def _data_pdfs() -> list[Path]:
    return sorted(DATA_DIR.glob("*.pdf")) if DATA_DIR.exists() else []
