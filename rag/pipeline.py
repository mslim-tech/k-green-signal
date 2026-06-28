# rag/pipeline.py
# -----------------------------------------------------------------------------
# in-app 인제스트 오케스트레이션의 '실행 기본기'
#
# 이 파일의 역할:
#   - 긴 LLM 단계(추출/표준화/정제…)를 Streamlit 을 막지 않고 돌리기 위해
#     각 단계를 '서브프로세스'로 띄우고, stdout/stderr 를 단계별 로그 파일로 캡처한다.
#   - 앱은 이 모듈의 launch()/tail()/alive() 로 단계를 실행하고 진행 로그를 읽는다.
#     (Popen 객체는 앱이 st.session_state 에 보관해 rerun 간 상태를 유지한다.)
#
#   왜 서브프로세스인가:
#     - 기존 rag/*.py 의 CLI(main)를 그대로 재사용(파이프라인 로직 재작성 없음).
#     - 스레드와 달리 LLM 호출 도중에도 트리째 취소(taskkill /T) 가능.
#     - Streamlit rerun/새로고침에도 별도 프로세스로 계속 진행.
#
# 단독 검증(빠른 단계 1개를 서브프로세스로 돌려 로그 캡처 확인):
#   uv run python rag/pipeline.py
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from rag.logging_setup import setup_logging
except ImportError:
    from logging_setup import setup_logging

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
try:
    from rag.paths import OUTPUT_DIR
except ImportError:
    from paths import OUTPUT_DIR
RUNS_DIR = OUTPUT_DIR / "runs"
STATE_FILE = OUTPUT_DIR / "ingest_state.json"   # 현재/마지막 인제스트 실행 1건(영속화)


@dataclass
class Step:
    key: str            # 단계 식별자
    title: str          # 사람용 제목
    module: str         # rag/<module>.py
    needs_pdf: bool      # extract 처럼 PDF 인자가 필요한가
    llm: bool            # LLM 호출(느림) 단계인가
    produces: str        # 완료 판정용 산출 파일(outputs/ 상대)


# 인제스트 체인: 업로드된 PDF → 추출 → 표준화 → 정제 → 검수 큐
INGEST_STEPS: list[Step] = [
    Step("extract",     "2. LLM 추출",        "extract",     True,  True,  ""),
    Step("standardize", "3. 표준화",          "standardize", False, True,  "standardized_long.csv"),
    Step("refine",      "4.1 라벨 표준화",     "refine",      False, True,  "standardized_long.clean.csv"),
    Step("dedup",       "4.2 중복 정리",       "dedup",       False, False, "standardized_long.dedup.csv"),
    Step("flags",       "4.3 의심값 플래그",    "flags",       False, True,  "standardized_long.flagged.csv"),
    Step("review",      "4.4 검수 큐",         "review",      False, False, "review_queue.csv"),
]
STEP_BY_KEY = {s.key: s for s in INGEST_STEPS}


DATA_DIR = PROJECT_ROOT / "data"


def _stem(pdf_name: str) -> str:
    return Path(pdf_name).stem


def step_output(step: Step, pdf_name: str | None) -> Path:
    """ 이 단계가 만드는 산출 파일. extract 만 PDF 별(stem)로 다르다. """
    if step.key == "extract":
        return OUTPUT_DIR / f"{_stem(pdf_name)}.extracted.jsonl"
    return OUTPUT_DIR / step.produces


def step_inputs(step: Step, pdf_name: str | None) -> list[Path]:
    """ 이 단계의 입력 파일들(이게 산출보다 새로우면 재실행 필요). """
    if step.key == "extract":
        return [DATA_DIR / pdf_name] if pdf_name else []
    if step.key == "standardize":
        return list(OUTPUT_DIR.glob("*.extracted.jsonl"))
    chain = {
        "refine": "standardized_long.csv",
        "dedup": "standardized_long.clean.csv",
        "flags": "standardized_long.dedup.csv",
        "review": "standardized_long.flagged.csv",
    }
    name = chain.get(step.key)
    return [OUTPUT_DIR / name] if name else []


def is_fresh(step: Step, pdf_name: str | None) -> bool:
    """ 산출이 이미 있고 모든 입력보다 새로우면 True(=이 단계는 건너뛰어도 됨). """
    out = step_output(step, pdf_name)
    if not out.exists():
        return False
    ins = step_inputs(step, pdf_name)
    if not ins or not all(p.exists() for p in ins):
        return False
    return out.stat().st_mtime >= max(p.stat().st_mtime for p in ins)


def new_run_id() -> str:
    return f"run_{datetime.now():%Y%m%d_%H%M%S}"


def run_dir(run_id: str) -> Path:
    d = RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def step_log_path(run_id: str, key: str) -> Path:
    return run_dir(run_id) / f"{key}.log"


def _argv(step: Step, pdf_name: str | None) -> list[str]:
    """ 단계별 실행 커맨드(.venv 파이썬으로 rag/<module>.py 호출). """
    script = str(PROJECT_ROOT / "rag" / f"{step.module}.py")
    argv = [sys.executable, script]
    if step.needs_pdf:
        if not pdf_name:
            raise ValueError(f"{step.key} 단계는 PDF 파일명이 필요합니다.")
        # extract.py 사용법: <파일경로> <개수> --save
        argv += [str(PROJECT_ROOT / "data" / pdf_name), "999", "--save"]
    return argv


def launch(run_id: str, key: str, pdf_name: str | None = None) -> subprocess.Popen:
    """ 단계를 서브프로세스로 띄우고, stdout+stderr 를 run 로그 파일로 캡처한다. """
    step = STEP_BY_KEY[key]
    logpath = step_log_path(run_id, key)
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
    }
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    logf = open(logpath, "w", encoding="utf-8")
    logf.write(f"$ {' '.join(_argv(step, pdf_name))}\n")
    logf.flush()
    proc = subprocess.Popen(
        _argv(step, pdf_name), cwd=str(PROJECT_ROOT), env=env,
        stdout=logf, stderr=subprocess.STDOUT, creationflags=creationflags,
    )
    log.info("launch step=%s run=%s pid=%s → %s", key, run_id, proc.pid, logpath)
    return proc


def alive(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def cancel_pid(pid: int | None) -> None:
    """ pid 로 프로세스 트리째 종료(복구 세션엔 Popen 이 없어 pid 로 죽인다). """
    if not pid:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True)
    except Exception:
        pass


def cancel(proc: subprocess.Popen) -> None:
    """ 프로세스 트리째 종료(Windows: taskkill /T). """
    try:
        cancel_pid(proc.pid)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def tail(path: Path, n: int = 50) -> str:
    """ 로그 파일 마지막 n 줄. UI 진행 표시/검증용. """
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-n:])


# --- 상태 영속화 + 복구 -------------------------------------------------------
# 인제스트 실행 상태(run_id/단계/타이밍/현재 단계 pid)를 디스크에 적어두면,
# 브라우저 새로고침으로 Streamlit 세션이 날아가도 앱이 다시 읽어 진행을 이어간다.
# (Popen 객체는 저장 못 하므로 pid 만 저장 → 복구 시 pid 생존·산출파일로 판정한다.)

def save_state(state: dict) -> None:
    """ 인제스트 실행 상태를 outputs/ingest_state.json 에 기록(원자적 교체). """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def load_state() -> dict | None:
    """ 저장된 인제스트 실행 상태(없으면 None). """
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def pid_alive(pid: int | None) -> bool:
    """ 해당 pid 프로세스가 살아있는가(Popen 없이 복구 후 확인용, Windows: tasklist). """
    if not pid:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        )
        return str(pid) in out.stdout
    except Exception:
        return False


def step_succeeded(step: Step, pdf_name: str | None, started_ts: float) -> bool:
    """ 복구 시 returncode 가 없으니, 단계 산출 파일이 시작 이후 갱신됐으면 성공으로 본다. """
    out = step_output(step, pdf_name)
    if not out.exists():
        return False
    # 시작 시각보다 약간 이른 갱신도 허용(파일시스템 mtime 해상도 여유 1s).
    return out.stat().st_mtime >= started_ts - 1


def recover_step_result(step: Step, pdf_name: str | None,
                        pid: int | None, started_ts: float) -> str | None:
    """ 복구 세션(Popen 없음)에서 단계의 끝남/성공 여부를 판정한다.
        반환: None(아직 진행 중) · 'ok'(끝남+성공) · 'fail'(끝남+실패). """
    if pid_alive(pid):
        return None
    return "ok" if step_succeeded(step, pdf_name, started_ts) else "fail"


def main() -> None:
    """ 검증: 빠른 단계(review)를 서브프로세스로 돌려 로그 캡처가 되는지 확인. """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    setup_logging("pipeline")

    run_id = new_run_id()
    print(f"run_id={run_id} — 'review' 단계를 서브프로세스로 실행해 로그 캡처 검증")
    proc = launch(run_id, "review")
    while alive(proc):
        time.sleep(0.5)
    print(f"returncode={proc.returncode}")
    print(f"로그 파일: {step_log_path(run_id, 'review')}")
    print("--- 로그 tail ---")
    print(tail(step_log_path(run_id, "review"), 15))


if __name__ == "__main__":
    main()
