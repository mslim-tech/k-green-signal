# ui/ingest.py
# -----------------------------------------------------------------------------
# 업로드(1단계) · 인제스트(2단계) 화면 + 진행 모니터. 긴 LLM 단계는 pipeline 서브프로세스로
# 돌려 UI 를 막지 않고 단계별 로그/진행을 캡처한다(Popen 은 st.session_state 에 보관).
# -----------------------------------------------------------------------------
from __future__ import annotations

import csv
import io
import logging
import re
import time

import streamlit as st
import pypdf   # 1단계 업로드에서 페이지 수 표시용

from rag import pipeline
from ui.common import DATA_DIR, _data_pdfs

logger = logging.getLogger("app")


# -----------------------------------------------------------------------------
# 단계별 화면
# -----------------------------------------------------------------------------
def render_step_upload(ctx: dict) -> None:
    st.subheader("📤 1단계 · 보고서 업로드")
    st.caption("분석할 인지도 조사 PDF를 올리면 `data/` 에 저장되고, 다음 단계(인제스트)의 입력이 됩니다.")

    up = st.file_uploader("PDF 업로드", type=["pdf"], accept_multiple_files=False)
    if up is not None:
        dest = DATA_DIR / up.name
        st.write(f"선택한 파일: **{up.name}** ({len(up.getvalue()):,} bytes)")
        if st.button("💾 data/ 에 저장", key="save_pdf"):
            DATA_DIR.mkdir(exist_ok=True)
            dest.write_bytes(up.getvalue())
            # 페이지 수 표시(검증/안내용)
            try:
                pages = len(pypdf.PdfReader(io.BytesIO(up.getvalue())).pages)
            except Exception:
                pages = "?"
            logger.info("업로드 저장: %s (%s pages)", up.name, pages)
            st.success(f"✅ 저장했습니다: data/{up.name} (페이지 {pages}). 2단계(인제스트)로 진행하세요.")
            st.rerun()

    st.markdown("**현재 `data/` 의 PDF:**")
    pdfs = _data_pdfs()
    if pdfs:
        for p in pdfs:
            st.write(f"- {p.name} ({p.stat().st_size:,} bytes)")
    else:
        st.caption("아직 없습니다.")


def _ingest_init(pdf_name: str | list[str], force: bool = False) -> None:
    """ 인제스트 체인 상태를 초기화하고 첫 단계를 띄운다. force=True 면 스킵 없이 전부 재실행.
        pdf_name 은 PDF 하나(str) 또는 여러 개(list) — extract 가 그 전부를 추출한다. """
    st.session_state.ingest = {
        "run_id": pipeline.new_run_id(),
        "pdf": pdf_name,
        "order": [s.key for s in pipeline.INGEST_STEPS],
        "idx": 0, "status": "running",
        "started": {}, "ended": {}, "rc": {}, "skipped": [], "pid": {},
        "force": force,
    }
    pipeline.save_state(st.session_state.ingest)
    _ingest_launch_current()


def _ingest_advance() -> None:
    """ 다음 단계로 넘긴다(없으면 완료). """
    ing = st.session_state.ingest
    ing["idx"] += 1
    if ing["idx"] >= len(ing["order"]):
        ing["status"] = "done"
        st.session_state.ingest_proc = None
        pipeline.save_state(ing)
    else:
        _ingest_launch_current()


def _ingest_recover() -> None:
    """ 새로고침으로 세션이 날아간 뒤, 디스크의 인제스트 상태가 'running'이면 복구한다.
        Popen 핸들은 못 살리므로, 복구 세션은 pid 생존/산출파일로 진행을 이어간다. """
    if st.session_state.get("ingest"):
        return                         # 세션에 이미 있으면(정상 진행 중) 복구 불필요
    saved = pipeline.load_state()
    if not saved or saved.get("status") != "running":
        return
    st.session_state.ingest = saved
    st.session_state.ingest_proc = None    # 복구엔 Popen 없음 → pid 경로로 모니터
    st.session_state.ingest_recovered = True
    st.session_state.step = 2              # 진행 중 인제스트를 볼 수 있게 2단계로
    logger.info("인제스트 복구: run=%s idx=%s", saved.get("run_id"), saved.get("idx"))


def _ingest_launch_current() -> None:
    """ 현재 단계를 실행한다. D1: 입력이 최신이면 LLM 호출 없이 '스킵'하고 다음으로. """
    ing = st.session_state.ingest
    key = ing["order"][ing["idx"]]
    step = pipeline.STEP_BY_KEY[key]
    ing["started"][key] = time.time()

    # 스킵 캐시: 강제 재실행이 아니고 산출이 최신이면 건너뛴다(연쇄적으로).
    if not ing.get("force") and pipeline.is_fresh(step, ing["pdf"]):
        ing["ended"][key] = ing["started"][key]
        ing["rc"][key] = 0
        ing["skipped"].append(key)
        st.session_state.ingest_proc = None
        logger.info("인제스트 단계 스킵(최신): %s", key)
        pipeline.save_state(ing)
        _ingest_advance()
        return

    proc = pipeline.launch(ing["run_id"], key, pdf_name=ing["pdf"] if key == "extract" else None)
    st.session_state.ingest_proc = proc
    ing.setdefault("pid", {})[key] = proc.pid    # 복구 시 pid 로 생존 확인
    pipeline.save_state(ing)
    logger.info("인제스트 단계 시작: %s (run=%s pid=%s)", key, ing["run_id"], proc.pid)


def _count_csv(path) -> int | None:
    """ CSV 데이터 행 수(헤더 제외). 없거나 깨지면 None. """
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))
    except Exception:
        return None


def _ingest_step_summary(key: str, pdf: str | None) -> str:
    """ 한 단계가 '무엇을 얻었나'를 한 줄로(앱 로그 디버깅용). 실패하면 조용히 ''.
        서브프로세스 로그(run 로그)와 별개로, 사람이 디버깅하는 앱 로그에 핵심 수치를 남긴다. """
    import json
    OUT = pipeline.OUTPUT_DIR
    try:
        if key == "extract":
            # extract 는 PDF 여러 개를 처리 → 모든 산출을 합산해 한 줄 요약.
            recs = []
            for p in pipeline.extract_outputs(pdf):
                if p.exists():
                    recs += [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
            blank = sum(1 for r in recs for it in (r.get("response_items") or [])
                        if it.get("value") in (None, ""))
            n_pdf = len(pipeline.extract_outputs(pdf))
            return f"PDF {n_pdf}개 · 블록 {len(recs)}개 · 빈값 항목 {blank}"
        prod = {
            "standardize": "standardized_long.csv",
            "refine": "standardized_long.clean.csv",
            "dedup": "standardized_long.dedup.csv",
            "flags": "standardized_long.flagged.csv",
            "review": "review_queue.csv",
            "refill_vision": "vision_candidates.csv",
        }.get(key)
        if not prod:
            return ""
        path = OUT / prod
        n = _count_csv(path)
        if key == "review":
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig", newline="")))
            blank = sum(1 for r in rows if not (r.get("value") or "").strip())
            return f"검수 큐 {n}행 · 값없음(빈칸) {blank}"
        if key == "flags":
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig", newline="")))
            fl = sum(1 for r in rows if any(r.get(c) == "True"
                     for c in ("flag_jump", "flag_mismatch", "flag_sum_violation")))
            return f"{n}행 · 의심 플래그 {fl}"
        if key == "refill_vision":
            return f"비전 빈칸-회수 후보 {n or 0}건(검수 확정 대기)"
        return f"{n}행"
    except Exception:
        return ""


def _extract_live(ing: dict) -> dict | None:
    """ 진행 중인 extract 의 구조화 진행 상태.
        완료 PDF 수는 산출 파일 존재로, 현재 PDF 의 블록 진행은 run 로그의 [n/m] 카운터로 읽는다.
        (extract 는 가장 긴 단계라 하위 진행바+평균속도로 병목을 한눈에 보이게 한다.) """
    outs = pipeline.extract_outputs(ing.get("pdf"))
    total_pdf = len(outs)
    if not total_pdf:
        return None
    done_files = [p for p in outs if p.exists()]
    cur_pdf = min(len(done_files) + 1, total_pdf)
    done_blocks = 0                       # 완료 PDF들의 누적 블록 수(= 추출 레코드 줄 수)
    for p in done_files:
        try:
            done_blocks += sum(1 for _ in open(p, encoding="utf-8"))
        except Exception:
            pass
    # 현재 PDF의 블록 카운터를 run 로그 tail 에서(단독 '[n/m]' 라인; '📄 [k/총]' PDF 헤더는 제외).
    tail = pipeline.tail(pipeline.step_log_path(ing["run_id"], "extract"), 40)
    blk_i = blk_n = None
    for line in tail.splitlines():
        m = re.match(r"^\s*\[(\d+)/(\d+)\]\s*$", line)
        if m:
            blk_i, blk_n = int(m.group(1)), int(m.group(2))
    return {"cur_pdf": cur_pdf, "total_pdf": total_pdf,
            "blk_i": blk_i, "blk_n": blk_n,
            "done_blocks": done_blocks + (blk_i or 0), "tail": tail}


@st.fragment(run_every=2)
def _ingest_monitor() -> None:
    """ 2초마다 현재 단계의 진행/로그를 갱신하고, 끝나면 다음 단계로 넘긴다. """
    ing = st.session_state.get("ingest")
    if not ing:
        return
    order, idx = ing["order"], ing["idx"]
    proc = st.session_state.get("ingest_proc")

    # 진행 중인 단계가 끝났으면 상태 전이(다음 단계 launch 또는 완료/에러).
    # 정상 세션은 Popen(returncode)으로, 복구 세션(Popen 없음)은 pid 생존+산출파일로 판정.
    if ing["status"] == "running":
        key = order[idx]
        if proc is not None:
            finished = not pipeline.alive(proc)
            rc = proc.returncode if finished else None
        else:
            # 복구 세션(Popen 없음): pid 생존+산출파일로 판정.
            res = pipeline.recover_step_result(
                pipeline.STEP_BY_KEY[key], ing["pdf"],
                ing.get("pid", {}).get(key), ing["started"].get(key, 0))
            finished = res is not None
            rc = None if res is None else (0 if res == "ok" else 1)
        if finished:
            ing["ended"][key] = time.time()
            ing["rc"][key] = rc
            logger.info("인제스트 단계 종료: %s rc=%s", key, rc)
            step = pipeline.STEP_BY_KEY[key]
            if rc == 0:
                # B1: 이 단계가 '무엇을 얻었나'를 앱 로그에 남긴다(디버깅 가능하게).
                summ = _ingest_step_summary(key, ing.get("pdf"))
                if summ:
                    logger.info("  └ %s 산출: %s", key, summ)
                _ingest_advance()
            elif step.optional:
                # 선택 단계(예: 비전 회수)는 실패해도 체인을 막지 않는다 — 앞의 검수 큐/게이트는 유효.
                logger.warning("선택 단계 %s 실패(rc=%s) — 무시하고 진행(검수 안 막음)", key, rc)
                _ingest_advance()
            else:
                ing["status"] = "error"
                pipeline.save_state(ing)
            # 체인이 끝났으면(완료/에러) 앱 전체를 리렌더한다. 이 모니터는 st.fragment 라
            # 프래그먼트만 재실행돼서, 바깥 main()의 build_ctx()가 새 산출물(review_queue.csv)을
            # 다시 읽지 못해 검수 게이트가 잠긴 채로 남는다. 앱 스코프 리렌더로 ctx 를 갱신한다.
            if ing["status"] in ("done", "error"):
                logger.info("인제스트 체인 %s — 검수 게이트 재평가(앱 리렌더)", ing["status"])
                st.rerun(scope="app")

    # 진행 표시
    if st.session_state.get("ingest_recovered") and ing["status"] == "running":
        st.info("↻ 새로고침 전 진행 중이던 인제스트를 이어받았습니다.")
    done = ing["idx"]
    total = len(order)
    frac = 1.0 if ing["status"] == "done" else min((done + 0.5) / total, 0.99)
    st.progress(frac, text=f"인제스트: {ing['status']} — {done}/{total} 단계 완료")

    for i, k in enumerate(order):
        s = pipeline.STEP_BY_KEY[k]
        if k in ing["ended"]:
            if k in ing.get("skipped", []):
                st.write(f"⏭️ {s.title} — 스킵(최신)")
                continue
            dt = ing["ended"][k] - ing["started"][k]
            mark = "✅" if ing["rc"].get(k) == 0 else "❌"
            st.write(f"{mark} {s.title} — {dt:.1f}s")
        elif i == done and ing["status"] == "running":
            dt = time.time() - ing["started"].get(k, time.time())
            st.write(f"▶ {s.title} … 진행 중 ({dt:.0f}s)")
            live = _extract_live(ing) if k == "extract" else None
            if live:
                # extract: 원문 로그 벽 대신 구조화 진행(현재 PDF·블록 하위바·평균속도).
                st.caption(f"📄 PDF {live['cur_pdf']}/{live['total_pdf']}")
                if live["blk_i"] and live["blk_n"]:
                    st.progress(min(live["blk_i"] / live["blk_n"], 1.0),
                                text=f"블록 {live['blk_i']}/{live['blk_n']}")
                avg = dt / max(1, live["done_blocks"])
                rem = ""
                if live["blk_i"] and live["blk_n"]:
                    rem = f" · 현재 PDF 남은 ~{int((live['blk_n'] - live['blk_i']) * avg)}s"
                st.caption(f"경과 {dt:.0f}s · 평균 {avg:.1f}초/블록{rem}")
                with st.expander("원문 로그 보기"):
                    st.code(live["tail"] or "(시작 중…)", language="log")
            else:
                # 그 외 단계(빠름): 하위 진행 신호가 없으니 로그 tail 을 그대로 보인다.
                tail = pipeline.tail(pipeline.step_log_path(ing["run_id"], k), 20)
                st.code(tail or "(시작 중…)", language="log")
        else:
            st.write(f"⏳ {s.title}")

    if ing["status"] == "done":
        st.success("✅ 인제스트 완료! 3단계(검수)에서 확인 후 4단계(인덱싱)로 진행하세요.")
        # 단계별 소요시간 표 — 어디가 병목인지 한눈에(엄밀 판단용).
        import pandas as pd
        recs = []
        for k in order:
            if k not in ing["ended"]:
                continue
            took = "스킵" if k in ing.get("skipped", []) else \
                f"{ing['ended'][k] - ing['started'][k]:.1f}s"
            recs.append({"단계": pipeline.STEP_BY_KEY[k].title, "소요": took})
        if recs:
            st.caption("단계별 소요시간 (병목 확인)")
            st.dataframe(pd.DataFrame(recs), hide_index=True, width="stretch")
    elif ing["status"] == "error":
        st.error(f"⛔ '{order[done]}' 단계 실패 (아래 🩺 시스템 로그/해당 단계 로그 확인). 원인 해결 후 다시 실행하세요.")
    elif ing["status"] == "cancelled":
        st.warning("취소되었습니다.")


def render_step_ingest(ctx: dict) -> None:
    st.subheader("⚙️ 2단계 · 인제스트 (추출 → 표준화 → 정제 → 검수 큐)")
    st.caption(
        "선택한 PDF에서 수치를 추출하고 표준화·정제해 검수 큐까지 만듭니다. "
        "⚠️ 표준화 이후는 `outputs/*.extracted.jsonl` 전체를 다시 처리하므로 여러 연도가 있으면 시간이 걸립니다."
    )

    pdfs = _data_pdfs()
    if not pdfs:
        st.warning("먼저 1단계에서 PDF를 업로드하세요.")
        return

    # 기본은 '업로드된 PDF 전체'를 추출한다(파일명이 연도로 시작 → 내림차순 표시).
    # 특정 문서를 빼려면 목록에서 그 항목만 지우면 된다(= 추출 제외).
    names = [p.name for p in sorted(pdfs, key=lambda p: p.name, reverse=True)]
    sel = st.multiselect(
        "추출할 PDF (기본: 전체 — 빼려면 항목을 지우세요)", names, default=names,
        help="업로드된 PDF 를 모두 추출합니다. 제외할 문서만 목록에서 지우세요.")
    force = st.checkbox("강제 재실행(스킵 안 함)", value=False,
                        help="끄면 산출이 최신인 단계는 건너뜁니다(빠름). 켜면 전부 다시 실행합니다.")
    ing = st.session_state.get("ingest")
    running = bool(ing and ing["status"] == "running")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶ 전체 실행", disabled=running or not sel, key="ingest_run"):
            _ingest_init(sel, force=force)
            st.rerun()
    with c2:
        if st.button("■ 취소", disabled=not running, key="ingest_cancel"):
            proc = st.session_state.get("ingest_proc")
            if proc is not None:
                pipeline.cancel(proc)
            elif ing:    # 복구 세션: Popen 없음 → 현재 단계 pid 로 종료
                key = ing["order"][ing["idx"]]
                pipeline.cancel_pid(ing.get("pid", {}).get(key))
            if ing:
                ing["status"] = "cancelled"
                pipeline.save_state(ing)
            logger.info("인제스트 취소")
            st.rerun()

    if ing:
        _ingest_monitor()
