# RAG Lab 진행 계획 (plan.md)

> 목표: 「친환경 생활·소비 국민 인지도 조사」 결과보고서(총 14개년: 2007, 2013~2025)를
> **정형 디지털 데이터셋(Long-format)** 으로 통합한다.
> 추출 범위는 우선 **'전체(국민 전체)' 핵심수치만** (하위집단 교차표는 추후).
>
> 갱신: 2026-06-25 · 방식: **연도별로 하나씩** 추가 (전체만 + 본문 서술 추출)
> 현재 완료: **2024·2025** 추출+표준화. 다음: 4.1 라벨 표준화 또는 2023 추가.

---

## ✅ 완료 (0~3단계 + 모델 설정)

- [x] **0 진단** `rag/ingestion.py` — 5개 모두 digital-text(OCR 불필요). 통계는 표가 아니라 서술문 인라인 수치 + 차트 이미지.
- [x] **1 블록분리** `rag/parsing.py` — 285 문항 블록. 신호: `Q.` / `섹션 N.` / `N)` / `(N=…)`·`[BASE…]` / `<그림·표 X-Y>`.
- [x] **2 LLM 추출** `rag/extract.py` — Structured Outputs(strict). `outputs/*.extracted.jsonl` (285건).
- [x] **3 표준화** `rag/standardize.py` — 배치 누적 사전. `outputs/question_dictionary.json`, `outputs/standardized_long.csv`.
- [x] **모델 중앙화** `rag/config.py` — 생성/추출/표준화 `gpt-5.4-mini`, 임베딩 `text-embedding-3-small`.
- [x] **2024·2025 데이터** — 각각 추출(2024 62블록 전부 high / 2025 55블록 high53·med2) + **2024+2025 표준화**(표준문항 65개, 51개가 2개년 연결, CSV 503행).

---

## 🔜 4단계: 데이터 정제·통합  (다음, 최우선 / 엄밀히 정의됨)

> 산출 목표: 신뢰할 수 있는 `outputs/standardized_long.clean.csv`

### 4.1 응답 라벨 표준화 (`rag/refine.py`)
- [ ] 4.1.1 표준문항(std_id)별로 등장한 응답 라벨 전부 수집
- [ ] 4.1.2 LLM(`STANDARDIZE_MODEL`)으로 동의 라벨 묶기 (예: `인지`=`알고있음`, `비인지`=`모름`)
- [ ] 4.1.3 라벨 사전 저장 `outputs/response_label_map.json`
- [ ] 4.1.4 CSV에 `std_response_label` 컬럼 추가 (원본 `response_label`은 보존)

### 4.2 2023 보고서 3종 중복 제거
- [ ] 4.2.1 중복 정의: `(year, std_id, std_response_label)` 동일 행을 중복으로 본다
- [ ] 4.2.2 우선순위 정책 적용 → **결정 필요(아래 ❓)**
- [ ] 4.2.3 dedup 실행 + 제거 내역 로그 `outputs/dedup_log.csv`

### 4.3 의심값 자동 플래그
- [ ] 4.3.1 전년 대비 급변(예: ±20%p 이상) 행에 `flag_jump=True`
- [ ] 4.3.2 `prev_year_note`의 수치와 실제 값 정합성 비교, 불일치 시 `flag_mismatch=True`
- [ ] 4.3.3 합계 검증: 단일응답(복수응답=아니오) 문항의 값 합이 100±오차인지 확인

### 4.4 저신뢰 검수 큐
- [ ] 4.4.1 `extraction_confidence ∈ {low, medium}` 또는 4.3 플래그가 있는 행만 추출
- [ ] 4.4.2 `outputs/review_queue.csv` 로 저장 (출처·페이지·원문 링크 포함)

---

## 5단계: 검수 UI (Streamlit) (엄밀히 정의됨)

- [ ] 5.1 `app.py`에 "검수" 탭 추가 (기존 Q&A와 분리)
- [ ] 5.2 `review_queue.csv`를 표로 표시, 행 선택 시 원본 PDF 페이지/그림 캡션 함께 보기
- [ ] 5.3 사람이 값 수정 → `outputs/corrections.jsonl`에 저장
- [ ] 5.4 정제 재실행 시 corrections를 우선 반영

---

## 6단계: RAG 검색·질의응답 시스템 (CLAUDE.md 파일 구조 준수)

> 정제된 데이터셋/원문 위에 자연어 질의응답을 올린다. 모델은 `config.py` 역할별 상수 사용.

- [ ] 6.1 **청킹** `rag/chunking.py` — 문항 블록 단위 청크. metadata 필수항목 유지(source/page/parser_type/chunk_id/token_count/warning)
- [ ] 6.2 **임베딩·인덱싱** `rag/index.py` — `EMBEDDING_MODEL`(text-embedding-3-small) + Chroma 벡터DB 구축
- [ ] 6.3 **검색** `rag/retriever.py` — 벡터 유사도 top-k 검색
- [ ] 6.4 **질문 재작성** — `REWRITE_MODEL`로 질의 정규화/확장
- [ ] 6.5 **Reranker** — `RERANKER_MODEL`로 검색 결과 재정렬
- [ ] 6.6 **답변 생성** — `ANSWER_MODEL`, 근거(출처·연도) 인용
- [ ] 6.7 **예시 질문** — `EXAMPLE_Q_MODEL`로 추천 질문 생성
- [ ] 6.8 평가셋 `eval/` — 표준화/검색 품질 점검용 질문 작성

---

## 7단계: 하위집단 교차표 추출 (보류 — 결정: '전체만' 유지)

> **결정(2026-06-25): 전체만 + 본문 서술 추출 유지. 하위집단은 당분간 안 함.**
>
> 참고로 하위집단(성별·연령·지역…) 데이터는 두 경로가 있다:
> - (권장) **부록 통계표** — 예: 2025 PDF p.121~144에 전체 교차표가 깔끔한 표로 존재.
>   `Docling`(TableFormer)으로 셀 구조까지 정확히 복원됨을 검증 완료. Vision 불필요.
>   Windows에선 `HF_HUB_DISABLE_SYMLINKS=1` 필요(심볼릭 링크 권한 오류 회피).
> - (대안) 본문 차트 이미지(`<그림>`)를 `VISION_MODEL`로 판독.
>
> 나중에 하위집단이 필요해지면 **Docling 부록표 경로**를 우선 검토. (`uv add docling`)

- [ ] 7.1 (보류) Docling으로 부록 통계표 → 구조화 표
- [ ] 7.2 (보류) long CSV에 `subgroup_type`/`subgroup_value` 차원 추가

---

## 8단계: 14개년으로 확장

- [ ] 8.1 옛 PDF 확보 (2007, 2013~2022) → **결정 필요(확보 경로)**
- [ ] 8.2 연도별 형식 차이 점검(메타 4번째 형식 등) → parsing 규칙 보강
- [ ] 8.3 동일 파이프라인(1→2→3→4) 적용, 연도 누적
- [ ] 8.4 표준 문항 사전에 옛 연도 매핑 (사전은 누적되므로 일관 유지)

---

## ❓ 사용자 결정이 필요한 항목 (이게 정해져야 위 단계가 '엄밀'해짐)

1. **4.2.2 2023 중복 우선순위**: 같은 문항이 통합본·주제별본에 모두 있을 때 무엇을 채택?
   - 후보: (a) 주제별 상세본 우선 (b) 통합본 우선 (c) `N`이 채워진 행 우선
2. ~~**7 하위집단 진행 여부**~~ → **결정됨: '전체만' 유지, 본문 서술 추출만 진행** (2026-06-25)
3. **8.1 옛 PDF 확보 경로**: 2007·2013~2022 보고서 원본을 어디서 받을지
