# ui/common.py
# -----------------------------------------------------------------------------
# 여러 UI 모듈이 공유하는 상수(산출물 파일 경로)와 공용 판정. app.py·ui/*.py 가 함께 쓰므로
# 한 곳에 둬 순환 import 를 피한다.
# -----------------------------------------------------------------------------
import os
from pathlib import Path

# 산출물 경로의 단일 소스 — RAG_OUTPUT_DIR(E2E 격리 등)를 따르도록 OUTPUT_DIR 를 쓴다.
# ("outputs" 하드코딩이면 파이프라인은 격리 폴더에 쓰는데 UI 만 실제 outputs 를 읽는 불일치.)
from rag.core.paths import OUTPUT_DIR

# 인제스트가 만든 검수 큐 파일 위치
REVIEW_QUEUE_PATH = OUTPUT_DIR / "review_queue.csv"
# 비전 재판독(refill_vision)이 제안한 '검토 후보' 파일 위치(값은 아직 데이터 아님).
VISION_CANDIDATES_PATH = OUTPUT_DIR / "vision_candidates.csv"

# 원본 PDF 디렉터리(업로드·인제스트·상태 카운트가 공유).
DATA_DIR = Path("data")


def _data_pdfs() -> list[Path]:
    return sorted(DATA_DIR.glob("*.pdf")) if DATA_DIR.exists() else []


def is_cloud() -> bool:
    """ Streamlit Community Cloud(배포)에서 실행 중인가.

    Cloud 는 앱을 `/mount/src/<repo>` 아래에 체크아웃한다(로컬 개발 경로는 이와 다름).
    이 판정으로 배포 웹에서는 '데이터 추가/인제스트'처럼 위험·휘발적인 관리 작업을 막는다
    (웹 작업 폴더는 재부팅 시 초기화되고, 전체 표준화는 기존 연도의 std_id 를 깨뜨린다 —
     새 연도 추가는 로컬 `docs/ADD_YEAR.md` 절차로만).
    RAG_FORCE_CLOUD 는 이 판정을 강제하는 테스트/검증용 훅(E2E·수동 확인).
    """
    if os.getenv("RAG_FORCE_CLOUD"):
        return True
    return str(Path(__file__).resolve()).replace("\\", "/").startswith("/mount/src")
