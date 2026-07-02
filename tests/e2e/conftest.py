# tests/e2e/conftest.py
# -----------------------------------------------------------------------------
# Playwright E2E 공용 픽스처: 헤드리스 Streamlit 서버를 띄우고 종료까지 관리한다.
#
#   - 세션당 한 번 streamlit 을 서브프로세스로 기동(포트 8599, UTF-8, RAG_FAKE_LLM=1).
#   - 서버 stdout/stderr 를 UTF-8 로그 파일로 캡처 → 테스트가 서버측 동작을 확인.
#   - /_stcore/health 가 200 이 될 때까지 폴링한 뒤 테스트 시작.
#   - 종료 시 Windows 프로세스 트리를 taskkill 로 정리(자식 orphan 방지).
#
# Windows 주의: PowerShell '>' 는 UTF-16 이라 깨지므로, 여기서 직접
#   open(logfile, encoding="utf-8") 로 stdout 을 받는다.
# -----------------------------------------------------------------------------

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

PORT = 8599
BASE_URL = f"http://localhost:{PORT}"
HEALTH = f"{BASE_URL}/_stcore/health"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"


def _free_port(port: int) -> None:
    """ 해당 포트를 LISTEN 중인 프로세스를 종료(트리째). 이전 테스트/수동 기동 잔재 정리. """
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                             capture_output=True, text=True).stdout
        pids = set()
        for line in out.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                pids.add(line.split()[-1])
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                           capture_output=True)
    except Exception:
        pass


def _wait_health(timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


@pytest.fixture(scope="session")
def server_log() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"streamlit_e2e_{datetime.now():%Y%m%d_%H%M%S}.log"


@pytest.fixture(scope="session")
def streamlit_server(server_log: Path):
    """ 세션 동안 헤드리스 streamlit 을 띄우고 종료까지 관리. base_url 을 돌려준다. """
    _free_port(PORT)

    # 산출물 격리: 실제 outputs/ 를 임시 폴더로 복제하고 서버가 그쪽을 쓰게 한다.
    #   → 인제스트 테스트가 파이프라인을 실제로 돌려 산출물을 재생성/덮어써도
    #     실제 outputs/(옛 연도 통합 데이터 등)는 절대 손상되지 않는다.
    iso_root = Path(tempfile.mkdtemp(prefix="kgs_e2e_outputs_"))
    iso_outputs = iso_root / "outputs"
    real_outputs = PROJECT_ROOT / "outputs"
    if real_outputs.exists():
        shutil.copytree(real_outputs, iso_outputs)
    else:
        iso_outputs.mkdir(parents=True)

    # 인제스트 스킵 캐시(pipeline.is_fresh)가 '전부 최신'으로 판정하도록 산출 mtime 을
    # 파이프라인 순서대로 증가시켜 정규화한다. 실제 outputs/ 는 재통합 이력 때문에
    # dedup(신규) > flagged/review(구) 로 mtime 이 어긋나 있어, 그대로 복제하면 flags 단계가
    # stale 로 잡혀 인제스트 스킵 테스트가 실제 단계를 돌려 버린다(→ 미완료·상태오염으로
    # 인제스트/스텝퍼 e2e 가 연쇄 실패). 격리 복제본만 손대며 실제 outputs 는 건드리지 않는다.
    _chain = ["standardized_long.csv", "standardized_long.clean.csv",
              "standardized_long.dedup.csv", "standardized_long.flagged.csv",
              "review_queue.csv"]
    _ordered = sorted(iso_outputs.glob("*.extracted.jsonl")) + \
        [iso_outputs / n for n in _chain]
    _base = time.time()
    for _i, _p in enumerate(_ordered):
        if _p.exists():
            os.utime(_p, (_base + _i, _base + _i))   # 뒤 단계일수록 더 새롭게(입력 < 산출)
    (iso_outputs / "ingest_state.json").unlink(missing_ok=True)   # 세션 시작은 깨끗하게

    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "RAG_FAKE_LLM": "1",          # E2E 는 LLM 호출 없이 결정적 스텁 사용
        "RAG_LOG_DIR": str(LOG_DIR),
        "RAG_OUTPUT_DIR": str(iso_outputs),   # 산출물 격리(실제 outputs 보호)
    }
    cmd = [
        "uv", "run", "streamlit", "run", "app.py",
        "--server.headless", "true",
        "--server.port", str(PORT),
        "--server.fileWatcherType", "none",
        "--logger.level", "info",
    ]
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    logf = open(server_log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd, cwd=str(PROJECT_ROOT), env=env,
        stdout=logf, stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

    try:
        if not _wait_health():
            logf.flush()
            raise RuntimeError(
                f"streamlit 이 {PORT} 에서 기동하지 못함. 로그: {server_log}"
            )
        yield BASE_URL
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        try:
            logf.close()
        except Exception:
            pass
        shutil.rmtree(iso_root, ignore_errors=True)   # 격리 산출물 임시폴더 정리


@pytest.fixture(scope="session")
def base_url(streamlit_server: str) -> str:
    """ pytest-playwright 가 page.goto('/') 를 풀 때 쓰는 base_url 을 우리 서버로. """
    return streamlit_server
