# ui/review.py
# -----------------------------------------------------------------------------
# 검수 화면(데이터 준비 3단계) — review_queue.csv 를 표로 보여주고, 사람이 값을
# 확인/수정하면 corrections.jsonl 에 기록한다. LLM 검증(adjudicate) 실행 버튼·비전 후보도 여기.
# 저장/적용 로직은 rag/curate/corrections.py 가 담당한다(여기선 화면만).
# -----------------------------------------------------------------------------
from __future__ import annotations

import csv
import logging
import os
import time

import streamlit as st

from rag.curate import corrections
from rag import pipeline
from ui.common import REVIEW_QUEUE_PATH, VISION_CANDIDATES_PATH

logger = logging.getLogger("app")   # app.py 와 같은 로거 이름(검수·adjudicate 로그 일관)


# -----------------------------------------------------------------------------
# 검수 탭 화면
#    - review_queue.csv 를 표로 보여주고, 행을 고르면 상세 + 수정 폼을 연다.
#    - 저장은 rag/curate/corrections.py 가 outputs/corrections.jsonl 에 한 줄씩 쌓는다.
# -----------------------------------------------------------------------------

# 검수 큐 캐시 — 파일 mtime 을 캐시 키로 써서, 재인제스트로 파일이 바뀌면 자동 무효화된다.
# max_entries=2: 최신 스냅샷만 유용한데 재인제스트마다 옛 스냅샷이 쌓이는 것 방지.
@st.cache_data(show_spinner=False, max_entries=2)
def _load_review_queue_cached(mtime: float):
    with open(REVIEW_QUEUE_PATH, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_review_queue():
    """ review_queue.csv 를 dict 리스트로 읽는다. 파일이 없으면 None. """
    if not REVIEW_QUEUE_PATH.exists():
        return None
    return _load_review_queue_cached(REVIEW_QUEUE_PATH.stat().st_mtime)


# 원문 페이지 미리보기 — extract_vision 의 렌더러 재사용. (source, page) 단위 캐시.
# dpi=150: 화면 확인용은 추출용 200 까지 불필요(렌더 속도·메모리 절약). max_entries 로 상한.
@st.cache_data(show_spinner=False, max_entries=32)
def _page_image(source: str, page: int, dpi: int = 150) -> bytes | None:
    """ 원문 PDF 의 해당 페이지를 PNG 로 렌더링. PDF 가 없거나 페이지가 없으면 None. """
    from rag.ingest.extract_vision import render_page_images, _resolve_pdf
    try:
        pdf = _resolve_pdf(source)
    except FileNotFoundError:
        return None                     # 샘플 클론에는 원본 PDF 가 없다 — 미리보기만 생략
    imgs = render_page_images(pdf, page, page, dpi=dpi)
    return imgs[0] if imgs else None


# 표에 보여줄 핵심 컬럼(31개 다 보여주면 복잡하므로 추렸다). 상세는 아래 패널에서.
TABLE_COLUMNS = [
    "review_priority", "year", "std_id", "std_response_label",
    "value", "unit", "review_reasons", "extraction_confidence", "warning",
]

# 검수 상태를 사람이 읽는 말로.
STATUS_LABELS = {
    corrections.STATUS_FIXED: "값 고침",
    corrections.STATUS_CONFIRMED: "원래 값 맞음",
    corrections.STATUS_SKIP: "보류",
    corrections.STATUS_LLM_VERIFIED: "LLM 검증 확정",
}


def effective_value(row, latest):
    """ 검수 정정이 '반영된' 값을 돌려준다.
        - 'fixed'(값 고침)·'llm_verified'(LLM 검증 확정)면 고친/확정된 값,
          그 외(confirmed/없음)면 원본 값. (인덱싱 시 apply_corrections 와 같은 규칙)
        review_queue 표에 '정정값'으로 보여주거나, 수정 폼 기본값으로 쓴다. """
    rec = latest.get(corrections.row_key(row) + ("value",))
    if rec and rec.get("status") in (corrections.STATUS_FIXED,
                                     corrections.STATUS_LLM_VERIFIED):
        return rec.get("new_value", "")
    return row.get("value", "")


def needs_value(row, latest) -> bool:
    """ 이 행이 '값이 비어 반드시 검수해야 하는' 행인가.
        반영값(정정 포함)이 숫자로 읽히지 않으면 True.

        단, 이미 처리된 행 — 사람이 '빈 값이 맞다(confirmed)'/'제외(skip)' 했거나
        LLM 검증(llm_verified)으로 확정된 행 — 은 더 이상 검수 대상이 아니다. """
    rec = latest.get(corrections.row_key(row) + ("value",))
    if rec and rec.get("status") in (corrections.STATUS_CONFIRMED, corrections.STATUS_SKIP,
                                     corrections.STATUS_LLM_VERIFIED):
        return False
    v = (effective_value(row, latest) or "").strip()
    if not v:
        return True
    try:
        float(v)
        return False
    except ValueError:
        return True


def render_detail_and_edit(row, latest):
    """ 선택한 행의 상세 정보 + 수정 폼을 그린다. """
    st.divider()
    st.markdown(f"### {row.get('std_id')} · {row.get('year')}년")

    left, right = st.columns(2)
    with left:
        st.write(f"**문항 요약:** {row.get('question_summary', '')}")
        st.write(f"**표준 응답 라벨:** {row.get('std_response_label', '')}")
        unit = row.get("unit", "")
        original = row.get("value", "")
        eff = effective_value(row, latest)
        if eff != original:
            # 정정이 반영된 경우: 원본 → 정정값을 한눈에 비교.
            st.write(f"**원본 값:** {original or '(빈값)'} {unit}  →  **정정값:** :green[{eff} {unit}]")
        else:
            st.write(f"**현재 값:** {original or '(빈값)'} {unit}")
        st.write(f"**섹션:** {row.get('section', '')} / {row.get('subsection', '')}")
    with right:
        st.write(f"**우선순위:** {row.get('review_priority', '')}")
        st.write(f"**검수 사유:** {row.get('review_reasons', '')}")
        st.write(f"**출처:** {row.get('source_locator', '')}")
        st.write(f"**추출 신뢰도:** {row.get('extraction_confidence', '')}")

    if (row.get("warning") or "").strip():
        st.warning(f"warning: {row.get('warning')}")

    # 4.3 플래그를 사람이 이해할 수 있게 풀어서 보여준다.
    flag_bits = []
    if row.get("flag_jump") == "True":
        flag_bits.append(f"전년 대비 급변 (이전 {row.get('prev_value')} → 현재 {row.get('value')}, Δ{row.get('yoy_delta')}%p)")
    if row.get("flag_mismatch") == "True":
        flag_bits.append(f"전년 노트와 모순: {row.get('mismatch_reason')}")
    if row.get("flag_sum_violation") == "True":
        flag_bits.append(f"보기 합계 {row.get('sum_total')} (100±5 벗어남)")
    if flag_bits:
        st.info(" · ".join(flag_bits))
    if (row.get("prev_year_note") or "").strip():
        st.caption(f"전년 대비 노트(원문): {row.get('prev_year_note')}")

    # 원문 페이지 미리보기 — "사람이 원문을 보고 확정"을 앱 안에서 완결한다.
    src = (row.get("source") or "").strip()
    ps = (row.get("page_start") or "").strip()
    pe = (row.get("page_end") or "").strip()
    title = f"📄 원문 페이지 보기 — {src} p.{ps}" + (f"-{pe}" if pe and pe != ps else "")
    with st.expander(title, expanded=True):
        if src and ps.isdigit():
            png = _page_image(src, int(ps))
            if png is not None:
                st.image(png, width="stretch", caption=f"{src} p.{ps}")
                if pe.isdigit() and int(pe) > int(ps):
                    st.caption(f"(표가 p.{ps}-{pe}에 걸쳐 있습니다 — 첫 페이지만 표시)")
            else:
                st.caption("원문 PDF 가 data/ 에 없어 페이지 미리보기를 표시할 수 없습니다. "
                           "위 '출처' 표기를 참고해 원문에서 직접 확인해 주세요.")
        else:
            st.caption("출처 페이지 정보가 없어 원문 미리보기를 표시할 수 없습니다.")

    # 이 행에 대한 직전 검수 기록이 있으면 보여준다(같은 행 재검수 참고용).
    prev = latest.get(corrections.row_key(row) + ("value",))
    if prev:
        st.caption(
            f"📝 이전 검수: {STATUS_LABELS.get(prev.get('status'), prev.get('status'))}"
            f" / 고친값={prev.get('new_value') or '-'} / 메모={prev.get('note') or '-'}"
            f" ({prev.get('ts')})"
        )

    # --- 수정 폼 ---
    # 행 식별키로 위젯 키를 고정 — 행 전환 시 직전 행의 입력값(메모 등)이 남지 않게.
    k = "_".join(corrections.row_key(row))
    with st.form(f"review_edit_{k}"):
        status = st.radio(
            "검수 결과",
            # 기본값을 '원래 값 맞음'으로 — 무심코 저장해도 '값 고침'으로 오기록하지 않게(안전 기본값).
            [corrections.STATUS_CONFIRMED, corrections.STATUS_FIXED, corrections.STATUS_SKIP],
            format_func=lambda s: STATUS_LABELS[s],
            horizontal=True, key=f"rv_status_{k}",
        )
        # 이미 정정된 값이 있으면 그 값을 기본으로 채워, 확인 후 그대로 저장만 누르면 되게 한다.
        new_value = st.text_input("고친 값 ('값 고침'일 때만 반영)",
                                  value=effective_value(row, latest), key=f"rv_value_{k}")
        note = st.text_input("메모(선택)", value=(prev.get("note", "") if prev else ""),
                             key=f"rv_note_{k}")
        reviewer = st.text_input("검수자(선택)", value=(prev.get("reviewer", "") if prev else ""),
                                 key=f"rv_reviewer_{k}")
        submitted = st.form_submit_button("💾 저장")
        if submitted:
            corrections.add_correction(
                row,
                status=status,
                # '값 고침'이 아니면 new_value 는 의미가 없으므로 빈 값으로 저장한다.
                new_value=new_value if status == corrections.STATUS_FIXED else "",
                note=note,
                reviewer=reviewer,
            )
            st.success("저장했습니다. (outputs/corrections.jsonl)")
            st.rerun()


def _render_sequential(filtered: list[dict], latest: dict, reviewed: set) -> None:
    """ 순차 검수: 필터된 목록에서 '미검수' 행만 한 건씩 보여준다.
        저장하면(add_correction→rerun) 그 행이 reviewed 로 빠지므로,
        같은 위치(pos)가 자연히 '다음 미검수 행'이 된다 — 별도 이동 로직 불필요. """
    pending = [r for r in filtered if corrections.row_key(r) not in reviewed]
    if not pending:
        st.success("이 필터에서 미검수 행이 없습니다 🎉 — 토글을 꺼서 전체 표를 확인하세요.")
        return
    pos = st.session_state.setdefault("review_seq_pos", 0)
    pos = min(max(pos, 0), len(pending) - 1)   # 필터 변경으로 목록이 줄어도 안전하게 클램프
    st.session_state.review_seq_pos = pos

    c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="center")
    c1.caption(f"미검수 {len(pending)}건 남음 · {pos + 1}/{len(pending)}번째 (저장하면 자동으로 다음 행)")
    if c2.button("◀ 이전", key="seq_prev", disabled=(pos == 0), width="stretch"):
        st.session_state.review_seq_pos = pos - 1
        st.rerun()
    if c3.button("다음 ▶", key="seq_next", disabled=(pos >= len(pending) - 1), width="stretch"):
        st.session_state.review_seq_pos = pos + 1
        st.rerun()

    render_detail_and_edit(pending[pos], latest)


def load_vision_candidates() -> list[dict]:
    """ vision_candidates.csv(비전 재판독 제안)를 dict 리스트로 읽는다. 없으면 빈 리스트.
        캐시하지 않는다 — 비전 재실행 시 즉시 최신을 반영해야 하고 파일이 작다. """
    if not VISION_CANDIDATES_PATH.exists():
        return []
    with open(VISION_CANDIDATES_PATH, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _candidate_row(c: dict) -> dict:
    """ 후보(candidate) 한 건을 corrections 식별 키(연도·std_id·표준응답라벨) 행으로 만든다. """
    return {
        "year": c.get("year", ""),
        "std_id": c.get("std_id", ""),
        "std_response_label": c.get("response_label", ""),
        "value": c.get("old_value", ""),   # old_value 추적용(보통 빈칸)
    }


def render_vision_candidates() -> None:
    """ 비전 재판독이 제안한 후보를 (제안값+출처)로 보여주고, 사람이 확정/기각하게 한다.
        확정 → corrections.jsonl(fixed) 로만 기록(정형 CSV 는 안 건드림 — '추측은 데이터가 아니다').
        이미 검수(확정/기각)된 후보는 숨긴다(반복 노출 방지). """
    cands = load_vision_candidates()
    if not cands:
        return
    reviewed = corrections.reviewed_keys()
    pending = [c for c in cands if corrections.row_key(_candidate_row(c)) not in reviewed]
    if not pending:
        return

    st.info(
        f"🔬 **비전 재판독 후보 {len(pending)}건** — 텍스트 추출이 놓쳤지만 페이지 "
        "**그림/표에서 읽어낸** 값 제안입니다. 원문 출처를 확인하고 확정하면 데이터로 반영됩니다."
    )
    for i, c in enumerate(pending):
        loc = f"{c.get('source','')} p.{(c.get('page') or '').split('-')[0]}"
        act = {"fill": "빈칸 채움", "inject": "누락행 추가"}.get(c.get("action"), c.get("action", ""))
        # 버튼 키는 위치(i)가 아니라 행 식별키로 — 확정/기각으로 목록이 줄어 순서가 밀려도
        # 클릭이 다른 후보에 붙지 않게 한다(붙으면 엉뚱한 값이 데이터로 확정됨).
        kid = f"{i}_" + "_".join(corrections.row_key(_candidate_row(c)))
        with st.container(border=True):
            st.markdown(
                f"**{c.get('std_id','')}** · {c.get('year','')}년 — "
                f"{c.get('response_label','')}"
            )
            st.write(
                f"제안값 **:green[{c.get('vision_value','')} %]**  ·  유형: {act}  ·  "
                f"신뢰도: {c.get('method','vision')}"
            )
            st.caption(f"출처: {loc}")
            b1, b2 = st.columns(2)
            with b1:
                if st.button("✅ 확정(데이터로 반영)", key=f"vc_ok_{kid}", type="primary"):
                    corrections.add_correction(
                        _candidate_row(c),
                        status=corrections.STATUS_FIXED,
                        new_value=c.get("vision_value", ""),
                        note=f"비전 재판독 확정 — {loc}",
                    )
                    st.success("확정 저장(corrections.jsonl). 4단계 재인덱싱 시 반영됩니다.")
                    st.rerun()
            with b2:
                if st.button("🚫 기각(제안 무시)", key=f"vc_no_{kid}"):
                    # 기각 = '제안을 무시하고 원래 값 유지'. skip 으로 기록하면 chunking 이
                    # 그 행 자체를 인덱스에서 제외해 버리므로(조용한 데이터 소실),
                    # confirmed(원래 값 유지)로 기록해 후보만 숨긴다.
                    corrections.add_correction(
                        _candidate_row(c),
                        status=corrections.STATUS_CONFIRMED,
                        note=f"비전 제안 기각(원래 값 유지) — {loc}",
                    )
                    st.rerun()
    st.divider()


def _adjudicate_launch(count: int) -> None:
    """ LLM 검증(adjudicate)을 서브프로세스로 시작하고 세션에 보관한다(길어서 앱을 막지 않게). """
    run_id = pipeline.new_run_id()
    proc = pipeline.launch_adjudicate(run_id, count)
    st.session_state.adjudicate = {"run_id": run_id, "started": time.time(),
                                   "count": count, "pid": proc.pid}
    st.session_state.adjudicate_proc = proc
    # 새로고침으로 세션이 날아가도 이어받도록 pid 를 영속화(같은 후보 재실행=이중 과금 방지).
    pipeline.save_state(st.session_state.adjudicate, pipeline.ADJ_STATE_FILE)
    logger.info("LLM 검증 시작: run=%s pid=%s count=%s", run_id, proc.pid, count)


def _adjudicate_recover() -> None:
    """ 새로고침으로 세션이 날아갔어도 진행 중 LLM 검증을 이어받는다(인제스트 복구와 같은 방식).
        Popen 은 저장할 수 없으므로 pid 생존으로 판정한다. """
    if st.session_state.get("adjudicate"):
        return
    saved = pipeline.load_state(pipeline.ADJ_STATE_FILE)
    if not saved:
        return
    if pipeline.pid_alive(saved.get("pid")):
        st.session_state.adjudicate = saved
        st.session_state.adjudicate_proc = None   # Popen 은 복구 불가 — pid 로만 관리
        st.info("↻ 새로고침 전 진행 중이던 LLM 검증을 이어받았습니다.")
    else:
        pipeline.ADJ_STATE_FILE.unlink(missing_ok=True)   # 이미 끝난 실행의 잔재 정리


@st.fragment(run_every=2)
def _adjudicate_monitor() -> None:
    """ 2초마다 LLM 검증 진행/로그를 갱신하고, 끝나면 게이트를 다시 계산(앱 리렌더). """
    adj = st.session_state.get("adjudicate")
    if not adj:
        return
    proc = st.session_state.get("adjudicate_proc")
    dt = time.time() - adj["started"]
    # 복구 세션엔 Popen 이 없으니 pid 생존으로 판정한다.
    running = pipeline.alive(proc) if proc is not None else pipeline.pid_alive(adj.get("pid"))
    if running:
        st.write(f"▶ 🤖 LLM 검증 진행 중 ({dt:.0f}s) — 최대 {adj['count']}건")
        tail = pipeline.tail(pipeline.step_log_path(adj["run_id"], "adjudicate"), 15)
        st.code(tail or "(시작 중…)", language="log")
        if st.button("■ 취소", key="adj_cancel"):
            if proc is not None:
                pipeline.cancel(proc)
            else:
                pipeline.cancel_pid(adj.get("pid"))
            pipeline.ADJ_STATE_FILE.unlink(missing_ok=True)
            st.session_state.adjudicate = None
            st.session_state.adjudicate_proc = None
            st.rerun(scope="app")
    else:
        # 크래시(rc≠0)를 '완료'로 보고하지 않는다 — 인제스트 모니터와 같은 정직성.
        # (복구 세션은 Popen 이 없어 rc 를 모름 → pid 종료를 완료로 간주할 수밖에 없음)
        rc = proc.returncode if proc is not None else None
        if rc not in (0, None):
            st.error(f"⛔ LLM 검증 실패 (rc={rc}) — 아래 🩺 시스템 로그/run 로그를 확인하세요.")
            logger.warning("LLM 검증 실패: run=%s rc=%s", adj["run_id"], rc)
        else:
            st.success(f"✅ LLM 검증 완료 ({dt:.0f}s). 게이트를 다시 계산합니다.")
            logger.info("LLM 검증 완료: run=%s", adj["run_id"])
        pipeline.ADJ_STATE_FILE.unlink(missing_ok=True)
        st.session_state.adjudicate = None
        st.session_state.adjudicate_proc = None
        st.rerun(scope="app")


def render_review_tab(gate=None):
    st.subheader("🔍 검수 큐")
    st.caption("인제스트가 고른 저신뢰 행을 사람이 확인/수정합니다. 원본 CSV 는 건드리지 않고 corrections.jsonl 에만 기록합니다.")
    _adjudicate_recover()   # 새로고침 전 진행 중이던 LLM 검증이 있으면 이어받는다.

    # 비전 재판독 후보(있으면)를 먼저 처리 — 검수 부담을 줄이는 핵심(원클릭 확정).
    render_vision_candidates()

    queue = load_review_queue()
    if queue is None:
        st.info("`outputs/review_queue.csv` 가 없습니다. 먼저 2단계(⚙️ 인제스트)에서 '▶ 전체 실행'을 눌러 검수 큐를 만들어 주세요.")
        return
    if not queue:
        st.success("검수할 행이 없습니다. 🎉")
        return

    # 검수 기록을 매번 새로 읽어 '검수 완료' 표시를 최신으로 유지한다(파일이 작아 빠름).
    recs = corrections.load_corrections()
    reviewed = corrections.reviewed_keys(recs)
    latest = corrections.latest_by_key(recs)

    # --- 인덱싱 게이트 '남은 할 일' 패널(게이트 ↔ 검수 연동) ---
    # 빈칸 배너만으론 '왜 아직 인덱싱이 잠겼는지'(예: 미검수 high 636)를 알 수 없다.
    # validate 의 권위 있는 카운트를 그대로 띄우고, review_queue 로 좁힐 수 있는 항목엔 집중 버튼을 준다.
    focus = st.session_state.get("review_focus")
    _gate = gate    # main() 이 이미 계산한 게이트 재사용(중복 청크 빌드 방지). 없으면 직접 계산.
    if _gate is None:
        try:
            from rag.curate.validate import validate_ready
            _gate = validate_ready(strict=True)
        except Exception:
            _gate = None
    if _gate is not None and not _gate.ok:
        with st.container(border=True):
            st.markdown(
                f"**🔒 인덱싱까지 남은 검수 — {len(_gate.blocking)}종 / 총 "
                f"{sum(c.count for c in _gate.blocking)}건**"
            )
            for c in _gate.blocking:
                st.write(f"- **{c.label}: {c.count}건** — {c.fix_hint}")
            has_high = any(c.id == "unreviewed_high" and c.count for c in _gate.blocking)
            b1, b2 = st.columns(2)
            if has_high and b1.button("🎯 미검수 high 행만 보기", key="focus_high"):
                st.session_state.review_focus = "high"
                st.rerun()
            if focus and b2.button("↩ 집중 해제(전체 보기)", key="focus_clear"):
                st.session_state.review_focus = None
                st.rerun()

            # 🤖 LLM 검증 실행기 — 불확실 항목을 원문(비전)으로 대조해 자동 확정/에스컬레이션.
            n_uncertain = next((c.count for c in _gate.blocking
                                if c.id == "unreviewed_high"), 0)
            st.divider()
            st.caption(
                f"🤖 **LLM 검증** — 불확실 high {n_uncertain}건을 원문 페이지(비전)로 독립 대조해 "
                "일치하면 자동 확정(llm_verified), 애매하면 사람에게 남깁니다. **실제 API 과금**."
            )
            # 🔑 실제 과금 단계 — 키 없으면 실행 자체를 막는다(RAG_FAKE_LLM 스텁은 무료라 허용).
            has_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("RAG_FAKE_LLM"))
            running = bool(st.session_state.get("adjudicate"))
            ac1, ac2 = st.columns([1, 2], vertical_alignment="bottom")
            with ac1:
                cnt = st.selectbox("검증 건수", [10, 50, 100, "전체"],
                                   key="adj_count", disabled=running)
            with ac2:
                if st.button("🤖 LLM 검증 실행", width="stretch", key="adj_run",
                             disabled=(running or not n_uncertain or not has_key)):
                    n = n_uncertain if cnt == "전체" else int(cnt)
                    _adjudicate_launch(n)
                    st.rerun()
            if not has_key:
                st.caption("🔑 `.env` 의 OPENAI_API_KEY 가 없어 실행할 수 없습니다(실제 API 과금 단계).")
            if st.session_state.get("adjudicate"):
                _adjudicate_monitor()
    elif _gate is not None and _gate.ok:
        st.success("✅ 인덱싱 게이트 통과 — 4단계에서 인덱싱할 수 있습니다.")
        # 집중 모드는 게이트 차단 행만 보여주므로, 게이트가 통과되면 자동 해제한다
        # (해제 버튼이 위 패널과 함께 사라져 목록이 빈 채로 잠기는 막다른 길 방지).
        if focus:
            st.session_state.review_focus = None
            focus = None
        # 진행 중 LLM 검증이 있으면 게이트 상태와 무관하게 모니터(취소·완료 감지)를 유지한다.
        if st.session_state.get("adjudicate"):
            _adjudicate_monitor()

    # --- D3: 값이 비어 반드시 검수해야 하는 행 안내(직접 찾지 않게) ---
    # 이미 사람이 '빈 값이 맞다(confirmed)'거나 '제외(skip)'로 처리한 행은 needs_value 가
    # 빼주므로(반복 노출 방지), 여기 남는 건 '아직 손대지 않은' 빈칸뿐이다.
    blank_rows = [r for r in queue if needs_value(r, latest)]
    if blank_rows:
        st.warning(
            f"⚠️ **값이 비어 검수가 필요한 행: {len(blank_rows)}건** — "
            "아래 '값 없는 행만 보기'로 모아 보고, 행을 골라 값을 채워주세요."
        )
    else:
        st.success(
            "✅ 값 없는 행이 모두 검수 처리되었습니다 "
            "(빈 값이 맞다고 확인했거나 제외한 행은 다시 띄우지 않습니다)."
        )
    only_blank = st.checkbox("값 없는 행만 보기", value=bool(blank_rows),
                             help="반영값이 비었거나 숫자가 아닌 행만 표시합니다.")

    # --- 필터 ---
    years = sorted({(r.get("year") or "") for r in queue})
    reasons_all = sorted({
        x for r in queue
        for x in (r.get("review_reasons") or "").split("; ") if x
    })
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        f_priority = st.multiselect("우선순위", ["high", "medium"], default=["high", "medium"])
    with c2:
        f_year = st.multiselect("연도", years, default=years)
    with c3:
        f_reason = st.multiselect("검수 사유(택1↑ 시 해당 행만)", reasons_all, default=[])
    with c4:
        hide_done = st.checkbox("미검수만 보기", value=False)

    def keep(r):
        # 게이트 집중 모드: 게이트가 실제로 차단하는 '불확실 high'만(validate 와 단일 소스).
        if focus == "high":
            from rag.curate.validate import is_uncertain_high
            return is_uncertain_high(r, reviewed)
        if only_blank and not needs_value(r, latest):
            return False
        if r.get("review_priority") not in f_priority:
            return False
        if (r.get("year") or "") not in f_year:
            return False
        if f_reason and not any(x in (r.get("review_reasons") or "") for x in f_reason):
            return False
        if hide_done and corrections.row_key(r) in reviewed:
            return False
        return True

    filtered = [r for r in queue if keep(r)]
    # 값 없는 행을 맨 위로(검수 우선) — 그 안에서는 원래 순서 유지(stable).
    filtered.sort(key=lambda r: 0 if needs_value(r, latest) else 1)
    done_n = sum(1 for r in queue if corrections.row_key(r) in reviewed)
    focus_note = " · 🎯 미검수 high 집중 중(위 '집중 해제'로 전체 보기)" if focus == "high" else ""
    st.caption(f"표시 {len(filtered)}행 / 전체 {len(queue)}행 · 검수 완료 {done_n}행 · "
               f"값없음 {len(blank_rows)}행{focus_note}")

    if not filtered:
        st.info("필터에 해당하는 행이 없습니다.")
        return

    # --- 순차 검수 모드 — 미검수 행을 한 건씩(저장하면 자동으로 다음 행) ---
    # st.dataframe 선택은 읽기 전용이라 '저장 후 다음 행 자동 선택'이 불가능하므로,
    # 미검수 목록 + 위치 포인터로 순차 진행한다. 토글을 끄면 기존 표(브라우즈)가 그대로.
    if st.toggle("🚀 순차 검수 모드 — 미검수 행을 한 건씩", key="review_seq"):
        _render_sequential(filtered, latest, reviewed)
        return   # 표와 동시에 그리지 않는다(수정 폼 중복 방지)

    # --- 표 (행 선택 가능) ---
    # 원본 값 옆에 '정정값'(검수 반영값)을 같이 보여줘 수치가 제대로 들어갔는지 눈으로 확인.
    import pandas as pd
    table_rows = []
    for r in filtered:
        row_view = {}
        for c in TABLE_COLUMNS:
            row_view[c] = r.get(c, "")
            if c == "value":
                eff = effective_value(r, latest)
                # 정정으로 값이 바뀐 경우만 따로 표시(같으면 빈칸으로 두어 시선 분산 방지).
                row_view["정정값"] = eff if eff != (r.get("value", "")) else ""
        row_view["확인필요"] = "🔴" if needs_value(r, latest) else ""
        row_view["검수"] = "✅" if corrections.row_key(r) in reviewed else ""
        table_rows.append(row_view)
    df = pd.DataFrame(table_rows)

    event = st.dataframe(
        df,
        selection_mode="single-row",
        on_select="rerun",
        hide_index=True,
        width="stretch",
        height=360,
    )
    selected = event.selection.rows
    if not selected:
        st.info("표에서 행을 선택하면 상세 정보와 수정 화면이 열립니다.")
        return

    render_detail_and_edit(filtered[selected[0]], latest)
