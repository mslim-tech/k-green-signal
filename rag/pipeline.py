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
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rag.core.logging_setup import setup_logging
from rag.core.paths import OUTPUT_DIR

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = OUTPUT_DIR / "runs"
STATE_FILE = OUTPUT_DIR / "ingest_state.json"   # 현재/마지막 인제스트 실행 1건(영속화)
ADJ_STATE_FILE = OUTPUT_DIR / "adjudicate_state.json"   # 진행 중 LLM 검증(새로고침 복구용)


@dataclass
class Step:
    key: str            # 단계 식별자
    title: str          # 사람용 제목
    module: str         # rag 하위 점 표기(예: ingest.extract) → `python -m rag.<module>`
    needs_pdf: bool      # extract 처럼 PDF 인자가 필요한가
    llm: bool            # LLM 호출(느림) 단계인가
    produces: str        # 완료 판정용 산출 파일(outputs/ 상대)
    optional: bool = False   # 실패해도 체인을 막지 않는 '있으면 좋은' 단계(예: 비전 회수)


# 인제스트 체인: 업로드된 PDF → 추출 → 표준화 → 정제 → 검수 큐 → (비전 빈칸 회수)
# refill_vision 은 '빈칸(추출 실패) 위치만' 비전으로 다시 읽어 '검토 후보'를 제안한다.
# canonical CSV 는 안 건드리며(추측 격리), 실패해도 앞의 검수 큐/게이트를 막지 않도록 optional.
# 타이틀에 번호를 붙이지 않는다 — 앱 화면의 '1~4단계' 번호와 이중 체계가 돼 혼란.
INGEST_STEPS: list[Step] = [
    Step("extract",     "LLM 추출",        "ingest.extract",       True,  True,  ""),
    Step("standardize", "표준화",          "transform.standardize", False, True,  "standardized_long.csv"),
    Step("refine",      "라벨 표준화",      "transform.refine",      False, True,  "standardized_long.clean.csv"),
    Step("dedup",       "중복 정리",        "transform.dedup",       False, False, "standardized_long.dedup.csv"),
    Step("flags",       "의심값 플래그",     "transform.flags",       False, True,  "standardized_long.flagged.csv"),
    Step("review",      "검수 큐",          "transform.review",      False, False, "review_queue.csv"),
    Step("refill_vision", "비전 빈칸 회수",  "curate.refill_vision",  False, True, "vision_candidates.csv", optional=True),
]
STEP_BY_KEY = {s.key: s for s in INGEST_STEPS}


DATA_DIR = PROJECT_ROOT / "data"


def _stem(pdf_name: str) -> str:
    return Path(pdf_name).stem


def _as_list(pdf_name: str | list[str] | None) -> list[str]:
    """ extract 는 PDF '여러 개'를 한 단계에서 처리한다. 단일 문자열/리스트/None 을 리스트로 통일. """
    if pdf_name is None:
        return []
    if isinstance(pdf_name, str):
        return [pdf_name]
    return list(pdf_name)


def extract_outputs(pdf_name: str | list[str] | None) -> list[Path]:
    """ extract 가 만드는 PDF 별 산출(*.extracted.jsonl) 목록. """
    return [OUTPUT_DIR / f"{_stem(p)}.extracted.jsonl" for p in _as_list(pdf_name)]


def step_output(step: Step, pdf_name: str | list[str] | None) -> Path:
    """ 이 단계가 만드는 산출 파일. extract 는 PDF 별(stem)이라 첫 PDF 기준(집합은 extract_outputs). """
    if step.key == "extract":
        outs = extract_outputs(pdf_name)
        return outs[0] if outs else OUTPUT_DIR / ".extracted.jsonl"
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
        # 비전 회수는 정제 산출(clean.csv)의 빈칸을 읽는다 → 그게 최신이면 다시 안 돈다(비용 방지).
        "refill_vision": "standardized_long.clean.csv",
    }
    name = chain.get(step.key)
    return [OUTPUT_DIR / name] if name else []


def is_fresh(step: Step, pdf_name: str | list[str] | None) -> bool:
    """ 산출이 이미 있고 모든 입력보다 새로우면 True(=이 단계는 건너뛰어도 됨). """
    if step.key == "extract":
        # extract 는 PDF 여러 개 → 모든 PDF의 산출이 각 원본보다 새로울 때만 스킵.
        pdfs = _as_list(pdf_name)
        if not pdfs:
            return False
        for p in pdfs:
            out = OUTPUT_DIR / f"{_stem(p)}.extracted.jsonl"
            src = DATA_DIR / p
            if not out.exists() or not src.exists():
                return False
            if out.stat().st_mtime < src.stat().st_mtime:
                return False
        return True
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


def _argv(step: Step, pdf_name: str | list[str] | None) -> list[str]:
    """ 단계별 실행 커맨드(.venv 파이썬으로 `python -m rag.<module>` 호출).
        cwd 는 launch() 에서 PROJECT_ROOT 로 지정하므로 rag 패키지가 해석된다. """
    argv = [sys.executable, "-m", f"rag.{step.module}"]
    if step.needs_pdf:
        pdfs = _as_list(pdf_name)
        if not pdfs:
            raise ValueError(f"{step.key} 단계는 PDF 파일명이 필요합니다.")
        # extract.py 사용법: <파일경로…> <개수> --save (파일 여러 개 = 모두 추출)
        argv += [str(PROJECT_ROOT / "data" / p) for p in pdfs] + ["999", "--save"]
    return argv


def launch(run_id: str, key: str, pdf_name: str | list[str] | None = None) -> subprocess.Popen:
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
    # POSIX 는 새 세션(=프로세스그룹)으로 띄워야 취소 시 자식까지 그룹째 종료할 수 있다.
    # (Windows 는 위 creationflags 가 같은 역할.)
    proc = subprocess.Popen(
        _argv(step, pdf_name), cwd=str(PROJECT_ROOT), env=env,
        stdout=logf, stderr=subprocess.STDOUT, creationflags=creationflags,
        start_new_session=(os.name != "nt"),
    )
    logger.info("launch step=%s run=%s pid=%s → %s", key, run_id, proc.pid, logpath)
    return proc


def launch_adjudicate(run_id: str, count: int) -> subprocess.Popen:
    """ LLM 검증(adjudicate)을 서브프로세스로 띄운다. 인제스트 체인과 별개인 1회성 작업이라
        상태머신 없이 이 함수로 직접 실행하고, 로그는 인제스트와 같은 run 로그로 캡처한다. """
    logpath = step_log_path(run_id, "adjudicate")
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
    }
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    argv = [sys.executable, "-m", "rag.curate.adjudicate", str(count)]
    logf = open(logpath, "w", encoding="utf-8")
    logf.write(f"$ {' '.join(argv)}\n")
    logf.flush()
    proc = subprocess.Popen(
        argv, cwd=str(PROJECT_ROOT), env=env,
        stdout=logf, stderr=subprocess.STDOUT, creationflags=creationflags,
        start_new_session=(os.name != "nt"),
    )
    logger.info("launch adjudicate run=%s pid=%s count=%s → %s", run_id, proc.pid, count, logpath)
    return proc


def alive(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def cancel_pid(pid: int | None) -> None:
    """ pid 로 프로세스 트리째 종료(복구 세션엔 Popen 이 없어 pid 로 죽인다). 플랫폼별. """
    if not pid:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True)
        except Exception:
            pass
        return
    # POSIX: launch 가 새 세션으로 띄웠으니 프로세스그룹째 종료(자식 포함).
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def cancel(proc: subprocess.Popen) -> None:
    """ 프로세스 트리째 종료(Windows: taskkill /T · POSIX: killpg). """
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

def save_state(state: dict, path: Path | None = None) -> None:
    """ 실행 상태를 JSON 으로 기록(원자적 교체). 기본은 인제스트(STATE_FILE),
        LLM 검증 등 다른 1회성 작업은 path 를 지정해 같은 방식으로 영속화한다.
        (기본값은 호출 시점에 해석한다 — 테스트가 STATE_FILE 을 monkeypatch 할 수 있게.) """
    path = path or STATE_FILE
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path | None = None) -> dict | None:
    """ 저장된 실행 상태(없으면 None). """
    path = path or STATE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def pid_alive(pid: int | None) -> bool:
    """ 해당 pid 프로세스가 살아있는가(Popen 없이 복구 후 확인용). 플랫폼별로 확인. """
    if not pid:
        return False
    if os.name == "nt":
        # Windows: tasklist 로 확인(POSIX 엔 없는 명령이라 분기).
        # tasklist 출력은 콘솔 코드페이지(한글이면 cp949)라, PYTHONUTF8=1 등
        # UTF-8 강제 환경에서 text=True 로 utf-8 디코드하면 0xc1 등에서 깨진다.
        # 우리는 ASCII 숫자 pid 만 필요하므로 errors="ignore" 로 안전하게 읽는다.
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
            )
            return str(pid) in out.stdout
        except Exception:
            return False
    # POSIX(macOS/Linux): 시그널 0 은 프로세스를 건드리지 않고 존재만 확인한다.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # 존재하지만 우리 소유가 아님(= 살아있음)
    except Exception:
        return False
    return True


def step_succeeded(step: Step, pdf_name: str | list[str] | None, started_ts: float) -> bool:
    """ 복구 시 returncode 가 없으니, 단계 산출 파일이 시작 이후 갱신됐으면 성공으로 본다. """
    if step.key == "extract":
        # extract 는 선택한 모든 PDF 의 산출이 시작 이후 갱신돼야 성공.
        outs = extract_outputs(pdf_name)
        return bool(outs) and all(
            o.exists() and o.stat().st_mtime >= started_ts - 1 for o in outs)
    out = step_output(step, pdf_name)
    if not out.exists():
        return False
    # 시작 시각보다 약간 이른 갱신도 허용(파일시스템 mtime 해상도 여유 1s).
    return out.stat().st_mtime >= started_ts - 1


def recover_step_result(step: Step, pdf_name: str | list[str] | None,
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
