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
- **웹 직접 업로드가 안 되는 이유**: Streamlit Cloud 작업 폴더는 재부팅 시 휘발되고 `samples/` 스탬프로 강제 재전개된다. 따라서 지속되는 갱신 경로는 로컬 검증 후 레퍼런스(samples/) 갱신 + push뿐이다. (배포 웹의 인제스트 실행은 `ui.common.is_cloud()` 가드로 막아 둔다 — 위험한 '전체 실행'을 렌더하지 않음.)

---

## 부록: 자동화(`scripts/add_year.py`) 설계

> 상태: **설계(뼈대)만 확정. 미구현.** 실행 코드는 검증 대상인 새 연도 PDF가 나왔을 때
> 그 파일로 실검증하며 완성한다(죽은 코드 방지). 위 1~11단계를 하나의 가이드 러너로 묶되,
> **사람 검문소 2개**(①매핑 dry-run 승인 ②검수)는 자동화하지 않는다 — std_id 보호와
> "추측은 데이터가 아니다" 원칙이 자동화 편의보다 우선하기 때문.

### 목적 / 범위
- **자동화 O**: 백업 → 추출 → 스테이징 → (비전 회수) → 매핑 → 적용 → std_id 불변 검증 →
  플래그·검수큐 → validate → 청킹·인덱싱
- **자동화 X(의도적)**: ⑤매핑 결과 승인(y/n) · ⑧사람 검수(원문 대조 확정) · ⑪배포 push
- **기존 CLI 재사용**: 각 단계는 검증된 `python -m rag.*` 진입점을 **서브프로세스**로 호출
  (앱 `rag/pipeline.py`와 같은 방식 — 실패 격리·로그 캡처). 통합/검증만 직접 import 로
  결과를 들여다보고 게이트한다.

### CLI 인터페이스(안)
```bash
uv run python scripts/add_year.py "data/2026....pdf" --year 2026 \
    [--refill-vision]     # 표 빵구 비전 회수(선택)
    [--adjudicate N]      # 검수 일부를 LLM 검증으로 자동확정(선택)
    [--from STAGE]        # 중단 지점부터 재개(extract|stage|map|apply|...)
    [--yes]               # 비대화형(CI). 단, 매핑 검문소는 --yes 여도 리포트 저장 후 정지가 기본
```

### 파이프라인 흐름 (본문 단계 매핑)
```
preflight   ─ 백업(outputs.bak_<year>) · API키 · PDF 존재 · 단일통합본 가정 확인
extract     ─ subprocess: rag.ingest.extract  (새 연도만)
stage       ─ mv outputs/<pdf>.extracted.jsonl → outputs/_staging_oldyears/
              (standardize 전체 재실행 격리 = std_id 보호)
[refill]    ─ subprocess: rag.curate.refill_vision           (--refill-vision 시)
map ★검문소 ─ subprocess: rag.curate.integrate_oldyears (map only)
              → 기존연결/신규 std_id 리포트 출력·저장 → 사람 y/n 승인(거부 시 종료, 파일 무변경)
apply       ─ subprocess: …integrate_oldyears --apply       (clean/dedup append)
verify ★    ─ std_id 불변 검증: 기존 연도 (year,std_id,label) 스냅샷 전/후 비교
              → 하나라도 바뀌면 자동 롤백(백업 복원) + 중단
flags/review─ subprocess: rag.transform.flags → rag.transform.review
REVIEW ★정지─ 사람 검수 안내 출력(앱 3단계 or --adjudicate). 자동 확정 안 함.
[adjudicate]─ subprocess: rag.curate.adjudicate N            (--adjudicate 시)
validate ★  ─ subprocess: rag.curate.validate → 게이트 통과 판정(미통과면 인덱싱 중단)
index       ─ subprocess: rag.retrieval.chunking → rag.retrieval.index
done        ─ 로컬 검증 안내(앱 실행) + '배포 반영은 11단계로 별도' 안내
```

### 스켈레톤 (스텁 — 로직 미구현)
```python
# scripts/add_year.py  ── 설계 뼈대(미구현). 새 연도 PDF 로 실검증하며 완성.
def preflight(pdf, year): ...            # 백업·키·PDF 확인
def run_extract(pdf): ...                # subprocess → extracted.jsonl 경로 반환
def stage(extracted): ...                # _staging_oldyears/ 로 이동
def run_map(): ...                       # integrate map(only), 리포트 파싱
def confirm_map(report, assume_yes): ... # ★사람 검문소(y/n)
def run_apply(): ...                     # integrate --apply
def snapshot_existing(year): ...         # 기존연도 (year,std_id,label) 키 집합
def verify_stdid_stable(before): ...     # ★불변 검증, 실패 시 rollback
def rollback(year): ...                  # outputs.bak_<year> 복원
def run_flags_review(): ...
def review_gate(adjudicate_n): ...       # ★검수 정지/안내
def run_validate(): ...                  # 게이트 판정(GateResult)
def run_index(): ...                     # chunking → index

STAGES = ["extract", "stage", "map", "apply", "verify", "flags_review",
          "review", "validate", "index"]   # --from 재개 지점

def main():
    # args 파싱 → preflight → STAGES 순회(--from 부터).
    # map 뒤 confirm_map()가 False면 종료. verify 실패면 rollback 후 종료.
    # validate 미통과면 index 진입 차단.
    ...
```

### 핵심 설계 결정
1. **서브프로세스 vs 직접 import** — 무거운 단계는 서브프로세스(기존 CLI 재사용·격리),
   매핑 리포트/std_id 검증만 직접 로직.
2. **검문소는 `--yes`로도 못 건너뜀** — 매핑 승인·검수는 데이터 무결성의 핵심이라 CI 에서도
   리포트 저장 후 정지가 기본(완전 무인 실행은 위험).
3. **배포 push 는 이 스크립트에 넣지 않음** — `--publish`(outputs→samples 복사+스탬프)까지는
   선택 가능하되, `git push` 는 항상 사람 손으로.
4. **std_id 불변 검증을 하드 게이트로** — 기존 연도 키가 하나라도 바뀌면 자동 롤백
   (본문 6단계의 수동 `grep` 검증을 코드화).

### 새 연도 PDF 나오면 검증할 것 (성공 기준)
- [ ] `integrate_oldyears._override_stdid` 하드코딩이 새 연도 문항을 오매핑하지 않는가(매핑 리포트 육안)
- [ ] extract 가 새 연도 레이아웃(페이지·표 라우팅)을 잡는가
- [ ] `--apply` 후 **기존 (year, std_id) 스냅샷 불변** 단언 통과
- [ ] 검수·validate 후 게이트 통과 → 대시보드에 새 연도 시계열 표시
- [ ] 비-LLM 단계는 `RAG_FAKE_LLM` 으로 스모크 가능(무과금 배선 검증)

---

## 부록: 보고서 시사점(implications) 추가 절차

> 각 연도 결과보고서 '요약·시사점(Executive Summary)/제언' 절의 **정성적 결론**을 정량 수치와
> 함께 인덱싱해, 챗봇(advise 모드)이 수치 나열을 넘어 "OOOO년 보고서 시사점에 따르면~"처럼
> 당시 연구원의 정책적 진단을 출처와 함께 인용하도록 하는 지식 소스다.
> `curation/methodology_notes.json`·`external_context.json` 과 같은 **큐레이션 지식 패턴**
> (파일 단일 소스 → 로더 → `parser_type` 지식청크). 데이터(정형 CSV)와는 별개이며 정형
> 파이프라인(std_id·게이트)과 무관하다 — 그래서 새 연도 추가와 독립적으로 갱신할 수 있다.

### 🚨 원칙
- **실제 보고서에 있는 문장만.** 요약·시사점/제언 절의 결론을 사람이 확인해 **원문 근거
  (연도·페이지)와 함께** 넣는다. 지어내거나 창작하지 않는다("추측은 데이터가 아니다").
- 확정 경로가 아니라 **큐레이션 지식**이므로 검수 큐·validate 게이트를 거치지 않는다.
  대신 사람이 원문 대조로 직접 확정해 `curation/implications.json` 에 커밋한다.

### 단계

**1) 원본 보고서에서 '요약·시사점/제언' 절 찾기**
Read 도구는 이 환경에서 PDF 렌더가 안 되므로(poppler 없음) PyMuPDF 로 텍스트를 뽑아 절을 찾는다.
```bash
uv run python -c "
import fitz
doc=fitz.open('data/2025년 ....pdf')
for i in range(doc.page_count):
    t=doc[i].get_text()
    if any(k in t for k in ['시사점','제언','조사 결과 요약']):
        print(i+1, [k for k in ['시사점','제언','조사 결과 요약'] if k in t])
"
```
- 최근 보고서: '조사 결과 요약' 절(예: 2024·2025)에 "…으로 해석할 수 있음/판단됨/예상됨" 형태의
  결론문이 있다. 2023 은 명시적 **'제언'** 절(보고서 p.33~35)이 있다.
- 각 결론문의 **관련 수치**와 **보고서 인쇄 쪽번호**('N｜' 패턴)를 함께 확인한다.

**2) `curation/implications.json` 의 `entries` 에 추가**
스키마(파일 상단 `_schema` 참고): `year` · `match`(키워드) · `std_id`(선택, 지표 링크) ·
`related_metric`(관련 수치) · `implication`(결론문, 원문 충실) · `source`(연도·보고서·절) · `page`.
```json
{
  "year": 2025,
  "match": ["녹색매장", "오프라인", "홍보"],
  "std_id": "녹색매장_인지도",
  "related_metric": "녹색매장 인지 수준 45.2%; 인지도 제고 '현판·안내판 표시 강화' 46.9%",
  "implication": "녹색매장 인지 수준이 높지 않아 적극적 커뮤니케이션이 요구되며, 매장 내 표시 강화 등 오프라인 홍보 강화 의견이 많은 것으로 진단함.",
  "source": "2025년 친환경생활·소비 국민 인지도 조사 결과보고서(조사 결과 요약)",
  "page": "16"
}
```

**3) 배선 검증(무과금)**
```bash
uv run python -c "
from rag.curate import implications as I
from rag.retrieval import chunking as C
print('항목', len(I.load_implications()), '| 청크', len(C.build_implication_chunks()))
"
```

**4) 재인덱싱 — 실 임베딩(과금)**
```bash
rm -rf outputs/chroma                        # 단일 컬렉션으로 깨끗이 재빌드(잔재 UUID 방지)
uv run python -m rag.retrieval.chunking      # chunks.jsonl 에 시사점 포함(… + 보고서 시사점 N)
uv run python -m rag.retrieval.index         # Chroma 재빌드
```
✔ `get_collection().get(where={'parser_type':'implication'})` 로 청크 수 확인.

**5) 실제 질문으로 인용 검증**
```bash
uv run python -m rag.retrieval.answer --advise "녹색매장 오프라인 홍보 관련 과거 진단?"
```
✔ 답변에 `[출처: …(조사 결과 요약/제언) p.NN]` 인용이 뜨고, 근거 출처 상위에 `implication__…` 청크가 잡히면 성공.

**6) 배포 반영(원하면)** — 본문 11단계와 동일 경로
```bash
cp outputs/chunks.jsonl samples/outputs/chunks.jsonl
rm -rf samples/outputs/chroma && cp -r outputs/chroma samples/outputs/chroma
printf 'full-2014-2025-rN' > samples/outputs/.dataset_version   # 스탬프 올림 → 클라우드 재전개
git add curation/implications.json samples/outputs && git commit && git push
```

### 관련 코드(배선은 이미 완료)
- `rag/curate/implications.py` — 로더(`external_context.py` 미러)
- `rag/retrieval/chunking.py` — `build_implication_chunks()`(parser_type='implication'), `build_all_chunks()`·`main()` 에 연결
- `rag/retrieval/answer.py` — `_advise_retrieve` 시사점 검색 축 + `ADVISE_SYSTEM_PROMPT` 규칙 7(인용 강제)
- 출처 표시는 `ui/rag.py` 기존 expander 가 자동 처리
> ⚠️ 과거에 `chunking.main()` 이 `build_all_chunks()` 를 안 써 시사점이 CLI 인덱싱에서 누락된
> 버그가 있었다(수정됨). 새 지식 종류를 추가하면 `main()` 과 `build_all_chunks()` 를 반드시 함께 갱신한다.
