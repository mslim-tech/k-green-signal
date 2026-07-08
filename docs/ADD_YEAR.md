# 새 연도 안전 추가 절차 (ADD_YEAR.md)

> 새 연도(예: 2026) 인지도 조사 결과보고서(PDF)를 기존 정형 데이터셋에 **안전하게 증분 추가**하는 로컬 절차.
> 핵심 목표: **기존 std_id(2014~2025)를 절대 흔들지 않고** 새 연도만 붙인 뒤, 검수 게이트를 통과한 것만 인덱싱해
> 대시보드·챗봇에 반영하고, 최종적으로 배포(Streamlit Cloud)까지 잇는다.
>
> 관련 문서: 전체 설계 [`ARCHITECTURE.md`](./ARCHITECTURE.md) · 파이프라인 표 [`../README.md`](../README.md) · 결정 기록 [`DECISIONS.md`](./DECISIONS.md).

---

## 🚨 절대 규칙 (먼저 읽기)

1. **`rag.transform.standardize`(전체 표준화)를 돌리지 않는다.**
   이것이 웹 스텝퍼(🛠 데이터 준비 2단계)의 표준화 단계이며, 전체를 다시 묶어 **2014~2025의 std_id를 비결정적으로 재배정**한다 →
   `corrections.jsonl`·`std_aliases`·routing·eval·테스트가 전부 깨진다. (코드 주석 실측: *"재실행 시 39개 std_id 사라짐"*, `rag/curate/integrate_oldyears.py:8`)
2. 대신 **추출까지만 정상 파이프라인**으로 하고, 그 뒤부터는 **증분 통합(`rag.curate.integrate_oldyears`)** 경로로 붙인다.
   이 CLI는 기존 `clean.csv`에서 std_id 사전을 **시드로 고정**(`load_curated_dict`)하고 새 연도 문항만 그 사전에 매핑해 **기존 행은 건드리지 않고 append** 한다.
3. **확정은 오직 검토 큐 경유** — 사람 검수 또는 `adjudicate`(LLM 비전 재판독) → `corrections.jsonl`.
   LLM·휴리스틱·비전의 값은 데이터가 아니라 **검토 후보**일 뿐이다("추측은 데이터가 아니다").
4. **배포 웹(Streamlit Cloud)에 PDF를 직접 올려 갱신하지 않는다.** 클라우드 작업 폴더는 휘발성이고 `samples/` 스탬프로 강제 재전개되므로, 웹 업로드 결과는 재부팅 때 사라진다. 갱신은 **로컬에서 검증 → `samples/` 갱신 → push**가 유일한 경로다.

> ℹ️ 참고: 2023~2025는 **단일 통합본**(친환경생활·소비 국민 인지도 조사)이다. 2026도 단일 통합본이면 아래 2~6단계를 파일 하나에 대해 한 번만 돌린다.
> 옛날처럼 **2종**(친환경제품 + 탄소/그린카드)으로 나오면 2~6단계를 두 파일에 대해 돌리고, 6단계 중복 통합(`_dedup_in_place`)이 겹치는 문항을 한 해로 합친다.

---

## 사전 준비 — 롤백 지점 만들기

문제가 생기면 되돌릴 스냅샷을 먼저 뜬다.

```bash
cp -r outputs outputs.bak_<연도>      # 예: outputs.bak_2026
```

`OPENAI_API_KEY`는 `.env`에 있어야 한다(추출·표준화 매핑·비전·임베딩에 필요, 과금됨).

---

## 1단계 · PDF 준비 + 진단

```bash
cp "2026년 ....pdf" data/
uv run python -m rag.ingest.ingestion        # 0 진단: 페이지/표 구조 확인
```

✔ **체크**: 파일이 `data/`에 있고, 진단이 표/그림 위치를 잡는지 확인.

## 2단계 · 추출 (새 연도만)

정상 추출기로 새 PDF **한 편만** 구조화 추출한다. (옛 연도용 `extract_vision_oldtable`이 아니라 일반 `extract` — 최근 연도처럼 정상 레이아웃이므로.)

```bash
uv run python -m rag.ingest.extract "data/2026년 ....pdf" 999 --save
# → outputs/2026년 ....extracted.jsonl 생성
```

✔ **체크**: jsonl의 블록 수와 빈값 항목 수를 본다. 표에서 값이 안 잡힌 '빵구'가 많으면 4단계에서 비전 회수.

## 3단계 · 스테이징으로 이동 (std_id 재배정 차단의 핵심)

추출 결과를 **standardize가 보는 `outputs/` 루트에서 빼내** 증분 통합 폴더로 옮긴다.
(루트에 남기면 나중에 실수로 standardize가 전체를 다시 묶을 위험이 있다.)

```bash
mkdir -p outputs/_staging_oldyears
mv "outputs/2026년 ....extracted.jsonl" outputs/_staging_oldyears/
```

## 4단계 · (선택) 표 빵구 비전 회수

2단계에서 표 값이 비어 있었다면 비전으로 값만 회수한다 → **검토 후보**로만 쌓인다(직접 데이터 아님).

```bash
uv run python -m rag.curate.refill_vision        # → vision_candidates.csv (검수에서 확정)
```

## 5단계 · 증분 매핑 (dry-run — 파일 변경 없음) ⭐가장 중요한 검문소

기존 std_id 사전을 시드로 새 연도 문항을 **매핑만** 하고 결과를 눈으로 검증한다.

```bash
uv run python -m rag.curate.integrate_oldyears        # --apply 없이 = map only
```

✔ **반드시 확인**할 출력:
- `[기존 std_id 로 연결된 문항]` — 새 연도 문항이 2014~2025의 올바른 std_id에 붙었는가(추세 연결).
- `[신규 std_id]` — 진짜 새 문항만 신규인가. **엉뚱하게 신규로 튄 문항 = 추세 끊김 신호**.
- ⚠️ `integrate_oldyears`에는 원래 옛 연도(탄소성적표지 과병합)용 하드코딩 교정(`_override_stdid`)이 들어 있다. 새 연도 문항이 이 규칙에 잘못 걸리면 매핑이 틀어질 수 있으니 매핑표를 특히 주의 깊게 본다. 틀어지면 여기서 멈추고(적용하지 않고) 별도 소규모 코드 보정으로 처리한다.

매핑이 이상하면: `outputs/_staging_oldyears/std_mapping.json`을 지우고 다시 map(LLM 재호출), 또는 `curation/mapping_review.csv` 워크시트로 결정적 교정.

## 6단계 · 적용 (clean/dedup에 append)

매핑이 옳다고 확인했을 때**만**:

```bash
uv run python -m rag.curate.integrate_oldyears --apply
# → clean.csv·dedup.csv 에 새 연도 행 append (기존 행 보존), 중복 통합
```

✔ **std_id 불변 검증**(지뢰를 밟지 않았는지):

```bash
# 직전 연도(예: 2025) std_id 집합이 통합 전후로 동일한지 — 달라졌으면 즉시 롤백
grep ",2025," outputs/standardized_long.dedup.csv | cut -d, -f1 | sort -u | wc -l
```

전후 개수가 같아야 정상. 다르면 `outputs.bak_<연도>`로 롤백한다.

## 7단계 · 플래그·검수 큐 재생성

새 연도 행에 대해 의심값/빈값을 검수 큐로 올린다(기존 corrections 오버레이는 유지).

```bash
uv run python -m rag.transform.flags         # 급변/서술정합/합계100 등 자동 플래그
uv run python -m rag.transform.review        # → review_queue.csv (새 연도 저신뢰·빈값 포함)
```

## 8단계 · 확정 (검수 게이트)

새 연도의 빈값·플래그·비전 후보를 **원문 보고 확정**한다. 두 경로 중 선택(병행 가능):

```bash
# (A) 사람 검수: uv run streamlit run app.py → 🛠 데이터 준비 → 3 검수
# (B) LLM 검증 자동확정(불확실 행을 비전 재판독해 명확한 것만 확정):
uv run python -m rag.curate.adjudicate 50    # → corrections.jsonl (status=llm_verified)
```

준비 게이트로 인덱싱 가능 여부를 확인한다:

```bash
uv run python -m rag.curate.validate         # 빈/미확정/미검수면 차단 항목 표시
```

✔ 게이트 통과(차단 0)까지 8단계를 반복한다.

## 9단계 · 청킹·재인덱싱

```bash
uv run python -m rag.retrieval.chunking      # → chunks.jsonl (새 연도 포함)
uv run python -m rag.retrieval.index         # → Chroma 재빌드(outputs/chroma/)
```

## 10단계 · 로컬 검증

```bash
uv run streamlit run app.py
```

✔ 🚦 대시보드에서 새 연도가 시계열 끝에 붙고 YoY 신호가 뜨는지, 💬 챗봇이 새 연도를 출처와 함께 답하는지 확인(실제 동작 검증이므로 `RAG_FAKE_LLM` 없이).
✔ 회귀 확인: `uv run pytest -q` — 기존 std_id 결합 테스트가 깨지지 않았는지.

## 11단계 · 배포 반영 (웹에 실제로 뜨게)

로컬 검증이 끝나야 웹에 올린다. 클라우드는 `samples/` 스탬프(`.dataset_version`)로 재전개하므로:

1. 검증된 작업 폴더를 레퍼런스로 복사한다 — `samples/outputs/`, (원본 PDF는 `.gitignore`로 제외되므로 `samples/data/`는 PDF 없이 산출물만).
2. 스탬프를 올린다(예):
   ```bash
   echo "full-2014-2026-r1" > samples/outputs/.dataset_version
   ```
3. `commit` & `push` → 클라우드의 `app.py._ensure_samples_bootstrapped`가 스탬프 불일치를 감지해 `outputs/`를 강제 재전개하고 chromadb 캐시를 비운다.

✔ samples 갱신 시 어떤 파일을 복사할지(원본 PDF 제외 규칙 등)는 실행 전에 확정한다.

---

## 요약 흐름

```
data/2026.pdf
  → extract (새 연도만)                    [정상 추출]
  → _staging_oldyears/ 로 이동              [standardize 격리 = std_id 보호]
  → integrate_oldyears (map→검증→apply)     [기존 사전 시드, 증분 append]
  → flags → review → 검수/adjudicate → validate   [검토 큐 경유 확정]
  → chunking → index                       [재인덱싱]
  → 로컬 검증 → samples 갱신+스탬프 → push    [웹 반영]
```

## 왜 이렇게 하는가 (근거)

- **두 개의 흐름 불변식**: 충실히 추출된 사실값만 정형 CSV로(실선), 불확실한 것은 검토 큐로(점선). 확정된 것만 `corrections.jsonl`로 실선에 오버레이. 새 연도 추가도 이 원칙을 그대로 따른다.
- **증분이 정석인 이유**: `standardize.py` 전체 재실행은 std_id를 처음부터 LLM으로 재생성해 기존 연결을 비결정적으로 깨뜨린다. `integrate_oldyears`는 기존 사전을 시드로 고정해 이 문제를 원천 차단한다.
- **웹 직접 업로드가 안 되는 이유**: Streamlit Cloud 작업 폴더는 재부팅 시 휘발되고 `samples/` 스탬프로 강제 재전개된다. 따라서 지속되는 갱신 경로는 로컬 검증 후 레퍼런스(samples/) 갱신 + push뿐이다.
