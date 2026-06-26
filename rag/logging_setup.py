# rag/logging_setup.py
# -----------------------------------------------------------------------------
# 전 모듈 공용 로깅 설정 (파일 + 콘솔, UTF-8)
#
# 이 파일의 역할:
#   - 파이프라인/앱이 "지금 무슨 일을 하는지"를 파일과 콘솔에 동시에 남긴다.
#   - 로그 파일은 logs/<name>_<ts>.log (UTF-8). 한글이 깨지지 않도록 인코딩 고정.
#   - Streamlit 은 상호작용마다 스크립트를 다시 실행(rerun)하므로, 핸들러가
#     중복으로 쌓이지 않게 멱등(idempotent)하게 설계한다(센티넬 가드).
#
#   왜 print 를 두고 logging 을 따로 쓰나:
#     - 기존 모듈의 print(사람용 진행 출력)는 그대로 둔다.
#     - logging 은 '기계가 읽는' 이벤트(시작/종료/카운트/에러)를 파일로 남겨,
#       나중에 추적·검증(테스트)할 수 있게 한다.
#
# 사용법:
#   from rag.logging_setup import setup_logging
#   logfile = setup_logging("app")          # 앱/모듈 시작점에서 한 번
#   import logging; log = logging.getLogger(__name__)
#   log.info("작업 시작")
# -----------------------------------------------------------------------------

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_SENTINEL = "_rag_handler"   # 우리 핸들러임을 표시 → rerun 시 중복 부착 방지
LOG_DIR = Path(os.getenv("RAG_LOG_DIR", "logs"))
FMT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(name: str = "run", level: str | None = None, console: bool = True) -> Path:
    """ 루트 로거에 파일+콘솔 핸들러를 (한 번만) 붙이고 로그 파일 경로를 돌려준다. """
    level = level or os.getenv("RAG_LOG_LEVEL", "INFO")
    root = logging.getLogger()
    root.setLevel(level)

    # 이미 우리 핸들러가 붙어 있으면(=rerun) 그대로 재사용한다.
    if any(getattr(h, _SENTINEL, False) for h in root.handlers):
        existing = getattr(root, "_rag_logfile", None)
        if existing:
            return Path(existing)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.log"

    fh = logging.FileHandler(logfile, encoding="utf-8")   # ← 한글 깨짐 방지(필수)
    fh.setFormatter(logging.Formatter(FMT, DATEFMT))
    setattr(fh, _SENTINEL, True)
    root.addHandler(fh)

    if console:
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter(FMT, DATEFMT))
        setattr(ch, _SENTINEL, True)
        root.addHandler(ch)

    setattr(root, "_rag_logfile", str(logfile))
    logging.getLogger(name).info("logging initialized → %s", logfile)
    return logfile


def current_logfile() -> Path | None:
    """ 현재 설정된 로그 파일 경로(없으면 None). UI 로그 패널이 tail 할 때 사용. """
    p = getattr(logging.getLogger(), "_rag_logfile", None)
    return Path(p) if p else None
