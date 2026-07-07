# app.py
# -----------------------------------------------------------------------------
# k-green-signal Streamlit 앱 — "결과 먼저, 관리 나중" 3모드 구조.
#
# 이 파일의 역할(오케스트레이션만 — 각 화면은 ui/ 모듈):
#   🚦 대시보드    : 신호등(연도별 추세). 정형 데이터가 있으면 여기가 첫 화면(랜딩).
#                   API Key·인덱스 없이도 동작한다(정형 CSV 만 읽음).
#   💬 AI에게 묻기 : 인덱스에서 검색해 출처 인용/데이터 기반 제언 답변(키 필요).
#   🛠 데이터 준비 : 1 업로드 → 2 인제스트 → 3 검수 → 4 인덱싱 스텝퍼(게이트 순서 유지).
# -----------------------------------------------------------------------------

import logging
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv


# 검수 기록 저장/적용 로직 (검수 화면이 corrections.jsonl 에 기록)
from rag.curate import corrections

# 공용 로깅 (파일+콘솔). 앱이 무슨 일을 하는지 logs/ 에 남긴다.
from rag.core.logging_setup import setup_logging

# 산출물 디렉터리(env RAG_OUTPUT_DIR 반영) — 랜딩 판단(rows_ready)에 쓴다.
from rag.core.paths import OUTPUT_DIR

# 인제스트 단계를 서브프로세스로 돌리는 러너(긴 LLM 단계가 UI 를 막지 않게).
from rag import pipeline

# 신호등 대시보드는 모듈로 분리(ui/signal.py).
from ui.signal import render_step_signal
# 검수 화면(3단계)은 모듈로 분리(ui/review.py).
from ui.review import render_review_tab, load_review_queue
# 공유 상수(검수 큐 경로) — build_ctx·_review_remaining_high 가 쓴다.
from ui.common import REVIEW_QUEUE_PATH, _data_pdfs
# 단계 화면들은 모듈로 분리(ui/).
from ui.rag import render_rag_tab
from ui.ingest import render_step_upload, render_step_ingest, _ingest_recover
from ui.index import render_step_index

logger = logging.getLogger("app")


# -----------------------------------------------------------------------------
# 1) 초기 설정: .env 에서 API Key 읽기
#    - 보안 규칙: API Key 는 .env 의 OPENAI_API_KEY 에서만 읽는다.
# -----------------------------------------------------------------------------
def get_api_key():
    """ .env 에서 OPENAI_API_KEY 를 읽어 반환한다. 없으면 None 을 반환한다. """
    load_dotenv()
    return os.getenv("OPENAI_API_KEY")


# -----------------------------------------------------------------------------
# 2) 3모드 — 🚦 대시보드 · 💬 AI에게 묻기 · 🛠 데이터 준비(업로드→인제스트→검수→인덱싱)
#    탭 대신 '순서가 있는 단계'로 안내한다. 각 단계는 앞 단계가 끝나야 열린다.
#    (각 단계 화면은 ui/ 모듈로 분리 — 여기선 순서·게이트·상태 오케스트레이션만)
# -----------------------------------------------------------------------------

NO_KEY_MSG = (
    "OPENAI_API_KEY 를 찾을 수 없습니다.\n\n"
    "프로젝트 폴더에 `.env` 파일을 만들고 `OPENAI_API_KEY=sk-...` 한 줄을 넣어주세요."
)

# 최상위 모드 — 결과(대시보드·질의)와 관리(데이터 준비)를 분리한다.
MODES = [
    ("signal", "🚦 대시보드"),
    ("qa", "💬 AI에게 묻기"),
    ("prep", "🛠 데이터 준비"),
]

# (번호, 라벨, 키) — 🛠 데이터 준비 스텝퍼의 화면 순서
STEPS = [
    (1, "📤 업로드", "upload"),
    (2, "⚙️ 인제스트", "ingest"),
    (3, "🔍 검수", "review"),
    (4, "📚 인덱싱", "index"),
]
# 추출/임베딩에 API Key 가 필요한 단계(질의 모드의 키 확인은 main 의 qa 분기에서)
STEPS_NEED_KEY = {2, 4}

# D2: 각 단계에서 '지금 무엇을 해야 하는지' 한 줄 안내(행동 유도)
STEP_TODO = {
    1: "분석할 보고서 PDF를 올리고 'data/ 에 저장'을 누르세요.",
    2: "'전체 실행'을 눌러 추출~검수큐까지 처리하세요. (완료까지 수 분 걸릴 수 있어요)",
    3: "🔴 값 없는 행부터 골라 원문을 보고 값을 확정(저장)하세요.",
    4: "준비 게이트를 통과하면 '인덱싱 실행'을 누르세요.",
}


def render_next_step_nav(ctx: dict, step: int) -> None:
    """ D2: 현재 단계를 마치면 다음 단계로 가는 버튼/안내. 마지막 단계 뒤엔 결과로 안내. """
    if step >= len(STEPS):
        # 데이터 준비 완료 → 결과(대시보드)로 돌아가는 고리.
        if ctx["index_count"] > 0:
            st.divider()
            if st.button("🚦 대시보드 보기", type="primary", key="goto_dashboard"):
                st.session_state.mode = "signal"
                st.rerun()
            st.caption("💬 AI에게 묻기에서 데이터에 대해 질문할 수도 있습니다.")
        return
    nxt = step + 1
    label = next(lbl for n, lbl, _ in STEPS if n == nxt)
    st.divider()
    if can_enter(nxt, ctx):
        # 라벨에 'N.' 번호는 넣지 않는다(상단 단계 네비 버튼과 텍스트 충돌 방지).
        if st.button(f"다음 단계로 → {label}", type="primary", key=f"goto_next_{step}"):
            st.session_state.step = nxt
            st.rerun()
    else:
        st.caption(f"🔒 다음 단계({label})는 이 단계를 끝내야 열립니다.")




@st.cache_resource
def _ensure_samples_bootstrapped():
    """ 클라우드/신규 클론 편의 — samples/ 레퍼런스를 작업 폴더 outputs/ 로 펼친다.

    Streamlit Community Cloud 는 재부팅 시 기존 체크아웃에 git pull 만 하고 작업 폴더
    (outputs/ — .gitignore)는 그대로 두므로, '비어 있을 때만 채우기'로는 데이터 업데이트가
    반영되지 않는다. 그래서 레퍼런스에 버전 스탬프(.dataset_version)를 두고, 배포본의
    스탬프와 다르면 강제로 다시 펼친다. @st.cache_resource 로 프로세스당 1회 실행.
      - outputs/ 가 비어 있으면            → 최초 전개(force=False)
      - 배포본 스탬프 ≠ 레퍼런스 스탬프    → 갱신(force=True, 이전 bootstrap 산출을 덮어씀)
      - 로컬 개발자의 직접 만든 outputs/   → 스탬프가 없으므로 건드리지 않는다(자기 작업 보호)
      - RAG_OUTPUT_DIR 지정(E2E 등)        → 미개입
    """
    if os.getenv("RAG_OUTPUT_DIR"):
        return
    root = Path(__file__).resolve().parent
    ref_stamp = (root / "samples" / "outputs" / ".dataset_version")
    cur_stamp = (OUTPUT_DIR / ".dataset_version")
    has_csv = ((OUTPUT_DIR / "standardized_long.dedup.csv").exists()
               or (OUTPUT_DIR / "standardized_long.clean.csv").exists())

    ref_ver = ref_stamp.read_text(encoding="utf-8").strip() if ref_stamp.exists() else None
    cur_ver = cur_stamp.read_text(encoding="utf-8").strip() if cur_stamp.exists() else None

    if not has_csv:
        force = False                         # 비어 있음 → 최초 전개
    elif cur_ver is not None and ref_ver is not None and cur_ver != ref_ver:
        force = True                          # bootstrap 이 만든 배포본이 낡음 → 갱신
    else:
        return                                # 최신이거나, 스탬프 없는 사용자 작업 → 미개입
    try:
        import importlib.util
        script = root / "scripts" / "bootstrap_samples.py"
        spec = importlib.util.spec_from_file_location("bootstrap_samples", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for line in mod.bootstrap(force=force):
            logger.info("bootstrap(force=%s): %s", force, line)
    except Exception:
        logger.exception("samples 부트스트랩 실패 — 대시보드 데이터가 없을 수 있음")


def _index_count() -> int:
    """ 현재 Chroma 인덱스의 청크 수(없으면 0). """
    try:
        from rag.retrieval.index import get_collection
        return get_collection().count()
    except Exception:
        return 0


def _review_remaining_high() -> int:
    """ 인덱싱을 실제로 막는 '불확실 high' 행 수(게이트와 단일 소스 — 완화된 고신뢰 행은 제외). """
    from rag.curate.validate import is_uncertain_high
    q = load_review_queue() or []
    reviewed = corrections.reviewed_keys()
    return sum(1 for r in q if is_uncertain_high(r, reviewed))


def build_ctx() -> dict:
    """ 단계 게이트/상태 패널이 쓰는 현재 데이터 상태. """
    return {
        "pdf_count": len(_data_pdfs()),
        "review_queue": REVIEW_QUEUE_PATH.exists(),
        "remaining_high": _review_remaining_high() if REVIEW_QUEUE_PATH.exists() else 0,
        "index_count": _index_count(),
        # 신호등이 읽는 정형 CSV 가 있는가(대시보드 랜딩 판단). OUTPUT_DIR 는 env 를
        # 따르므로(E2E 격리) chunking.SOURCE_CSV(임포트 시 고정) 대신 직접 확인한다.
        "rows_ready": (OUTPUT_DIR / "standardized_long.dedup.csv").exists()
                      or (OUTPUT_DIR / "standardized_long.clean.csv").exists(),
    }


def can_enter(step_no: int, ctx: dict) -> bool:
    """ 이 단계에 들어갈 수 있는가(앞 단계 산출물이 준비됐는가). """
    if step_no == 1:
        return True
    if step_no == 2:
        return ctx["pdf_count"] > 0          # 업로드된 PDF 가 있어야 인제스트
    if step_no in (3, 4):
        return ctx["review_queue"]           # 인제스트 산출(검수 큐)이 있어야
    return False


def _is_done(step_no: int, ctx: dict) -> bool:
    if step_no == 1:
        return ctx["pdf_count"] > 0
    if step_no == 2:
        return ctx["review_queue"]
    if step_no == 3:
        return ctx["review_queue"] and ctx["remaining_high"] == 0
    if step_no == 4:
        return ctx["index_count"] > 0
    return False


def _step_state(step_no: int, ctx: dict, current: int) -> str:
    if step_no == current:
        return "current"
    if _is_done(step_no, ctx):
        return "done"
    if can_enter(step_no, ctx):
        return "open"
    return "locked"


# -----------------------------------------------------------------------------
# 모드 네비 + 스텝퍼 헤더(네비) + 상태 패널 + 시스템 로그 패널
# -----------------------------------------------------------------------------
def render_mode_nav() -> None:
    """ 최상위 모드 버튼 3개. 항상 활성 — 준비 안내는 각 화면 안에서 한다(잠금 없음). """
    current = st.session_state.mode
    # Playwright/검증용 상태 센티넬(현재 모드)
    st.markdown(
        f"<span data-testid='mode-status' style='display:none'>{current}</span>",
        unsafe_allow_html=True,
    )
    cols = st.columns(len(MODES))
    for col, (key, label) in zip(cols, MODES):
        with col:
            icon = "▶" if key == current else "○"
            if st.button(f"{icon} {label}", key=f"mode_{key}", width="stretch"):
                st.session_state.mode = key
                st.rerun()


def render_stepper_nav(ctx: dict, current: int) -> None:
    cols = st.columns(len(STEPS))
    icon = {"current": "▶", "done": "✅", "open": "○", "locked": "🔒"}
    for col, (no, label, _key) in zip(cols, STEPS):
        with col:
            state = _step_state(no, ctx, current)
            # Playwright/검증용 상태 센티넬
            st.markdown(
                f"<span data-testid='step{no}-status' style='display:none'>{state}</span>",
                unsafe_allow_html=True,
            )
            if st.button(f"{icon[state]} {no}. {label}", key=f"nav_{no}",
                         disabled=(state == "locked"), width="stretch"):
                st.session_state.step = no
                st.rerun()


def render_status_panel(ctx: dict, gate=None) -> None:
    with st.sidebar:
        st.header("📊 데이터 상태")
        st.metric("업로드된 PDF", ctx["pdf_count"])
        st.metric("인덱싱된 청크", ctx["index_count"])
        st.metric("검수 남은 high", ctx["remaining_high"])

        # D5: 인덱스 정합(현재 데이터가 게이트를 통과하는가) — 좁은 폭에서 단어가 잘리지
        # 않게 라벨/내용을 짧은 줄로 나눈다(마크다운 hard break).
        if ctx["index_count"] > 0 and gate is not None:
            if gate.ok:
                st.success("✅ **인덱스 정합**  \n게이트 통과 데이터")
            else:
                st.warning("⚠️ **인덱스 정합** — 미통과  \n"
                           "미확정·빈값·미검수 포함 가능  \n검수 후 재인덱싱 권장")

        st.divider()
        # 다음 할 일 안내(모드 인지형). 좁은 사이드바에서 단어가 중간에 잘리지 않게
        # '무엇을(굵게)'과 '어떻게'를 짧은 두 줄로 나눈다(마크다운 hard break: 두 칸+개행).
        if ctx["pdf_count"] == 0:
            nxt = "🛠 **데이터 준비 1단계**  \nPDF를 업로드하세요."
        elif not ctx["review_queue"]:
            nxt = "🛠 **데이터 준비 2단계**  \n인제스트를 실행하세요."
        elif ctx["remaining_high"] > 0:
            nxt = f"🛠 **데이터 준비 3단계**  \nhigh {ctx['remaining_high']}건을 검수하세요."
        elif ctx["index_count"] == 0:
            nxt = "🛠 **데이터 준비 4단계**  \n인덱싱하세요."
        else:
            nxt = ("준비 완료 ✅  \n"
                   "🚦 **대시보드**에서 추세 보기  \n"
                   "💬 **AI에게 묻기**로 질문하기")
        st.info(f"👉 **다음 할 일**  \n{nxt}")


def render_log_panel() -> None:
    st.divider()
    with st.expander("🩺 시스템 로그", expanded=False):
        # (1) 진행 중인 인제스트 단계의 실시간 로그(run 로그)
        ing = st.session_state.get("ingest")
        if ing:
            key = ing["order"][min(ing["idx"], len(ing["order"]) - 1)]
            runlog = pipeline.step_log_path(ing["run_id"], key)
            st.caption(f"인제스트 run 로그: {runlog.name}")
            st.code(pipeline.tail(runlog, 30) or "(아직 없음)", language="log")

        # (2) 앱 로그
        lf = st.session_state.get("logfile")
        st.caption(f"앱 로그: {lf}")
        if lf and Path(lf).exists():
            tail = "\n".join(
                Path(lf).read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
            )
            st.code(tail or "(로그 비어 있음)", language="log")
        else:
            st.caption("앱 로그 파일이 아직 없습니다.")


# -----------------------------------------------------------------------------
# 3) 진입점 — 모드 구성(대시보드/질의/데이터 준비) + 현재 화면 렌더링
# -----------------------------------------------------------------------------
def main():
    # 로깅 먼저(멱등). 로그 파일 경로는 세션에 보관(로그 패널이 tail).
    logfile = setup_logging("app")
    st.session_state.setdefault("logfile", str(logfile))
    st.session_state.setdefault("step", 1)
    # 신규 클론·클라우드 배포에서 outputs/ 가 비어 있으면 samples/ 레퍼런스를 펼친다
    # (build_ctx 가 정형 CSV 를 읽기 전에 — 프로세스당 1회).
    _ensure_samples_bootstrapped()
    _ingest_recover()    # 새로고침으로 세션이 날아갔어도 진행 중 인제스트를 이어받는다.

    st.set_page_config(page_title="k-green-signal", layout="wide")
    st.title("🚦 대한민국 친환경 소비 인지도 실시간 신호등")
    st.caption("k-green-signal · 🚦 대시보드 — 💬 AI에게 묻기 — 🛠 데이터 준비(업로드→인제스트→검수→인덱싱)")

    api_key = get_api_key()
    ctx = build_ctx()
    # 결과 먼저: 정형 데이터가 있으면 대시보드로, 없으면 데이터 준비로 랜딩.
    # (_ingest_recover 가 prep 으로 정했을 수 있으므로 setdefault — 덮어쓰지 않는다.)
    st.session_state.setdefault("mode", "signal" if ctx["rows_ready"] else "prep")
    # 렌더 로그는 상태가 실제로 바뀔 때만 남긴다. Streamlit 은 상호작용마다 rerun 하므로
    # 매 렌더에 찍으면 동일 줄이 쌓여 로그의 신호(실제 상태 전이)가 묻힌다.
    render_state = (st.session_state.mode, st.session_state.step,
                    ctx["pdf_count"], ctx["index_count"], bool(api_key))
    if st.session_state.get("_last_render_state") != render_state:
        st.session_state["_last_render_state"] = render_state
        logger.info("앱 렌더 — mode=%s, step=%s, pdf=%s, idx=%s, api_key=%s",
                 st.session_state.mode, st.session_state.step,
                 ctx["pdf_count"], ctx["index_count"], "있음" if api_key else "없음")

    # 준비 게이트는 인덱스 정합 경고(D5)·검수(3)·인덱싱(4)에서 공유하므로 한 번만 계산.
    gate = None
    if ctx["review_queue"]:
        try:
            from rag.curate.validate import validate_ready
            gate = validate_ready(strict=True)
        except Exception:
            gate = None

    render_status_panel(ctx, gate)
    render_mode_nav()
    st.divider()

    mode = st.session_state.mode
    if mode == "signal":
        render_step_signal(ctx)

    elif mode == "qa":
        # RAG_FAKE_LLM(결정적 스텁)은 과금이 없으므로 키 없이 허용(E2E·데모용).
        if api_key is None and not os.getenv("RAG_FAKE_LLM"):
            st.error(NO_KEY_MSG)
        elif ctx["index_count"] == 0:
            st.info("아직 인덱스가 없습니다. 🛠 데이터 준비에서 업로드→인제스트→검수→인덱싱을 "
                    "마치면 AI에게 물어볼 수 있습니다.")
            if st.button("🛠 데이터 준비로 이동", key="goto_prep_from_qa"):
                st.session_state.mode = "prep"
                st.rerun()
        else:
            render_rag_tab(gate)

    else:   # prep — 데이터 준비 스텝퍼(1 업로드 → 2 인제스트 → 3 검수 → 4 인덱싱)
        render_stepper_nav(ctx, st.session_state.step)
        step = st.session_state.step
        # D2: 이 단계에서 '지금 할 일' 한 줄 안내(설명은 각 단계 화면 상단 caption 에).
        if step in STEP_TODO:
            st.info(f"👣 지금 할 일 — {STEP_TODO[step]}")

        if step in STEPS_NEED_KEY and api_key is None:
            st.error(NO_KEY_MSG)
        elif step == 1:
            render_step_upload(ctx)
        elif step == 2:
            render_step_ingest(ctx)
        elif step == 3:
            render_review_tab(gate)
        elif step == 4:
            render_step_index(ctx, gate)

        # D2: 다음 단계로 가는 행동 안내(마지막 단계 뒤엔 대시보드로 돌아가는 고리)
        render_next_step_nav(ctx, step)

    render_log_panel()


if __name__ == "__main__":
    main()
