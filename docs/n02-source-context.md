# N-02 원문 문맥 조회와 EvidenceBundle

> **버전 안내 (2026-11-05):** 아래 내용은 이전 구현의 설명·검증 기록이다. 현재의 청크별 근거 구조, 주장 출력과 보정 호출은 [최신 JM 계약 및 실행 방법](js-contract-migration.md)을 따른다. 예전 `items/evidence_id/citations` 및 문맥 병합 예제를 현재 API에 그대로 적용하지 않는다.

현재 JM의 PyMuPDF 텍스트 데모를 위한 N-02 구현 안내이다. 검색·답변 생성·의미적 사실 검증을 수행하는 노드는 아니다.

## 실행 순서와 search.py의 위치

T-01의 순서는 **N-01 → N-02 → N-04 → N-03**이다. ID 번호순이 아니다.

- `bm25.py`는 검색 엔진이다. N-01 `retrieve_evidence.py`가 검색 요청과 공통 반환 계약을 연결한다.
- 현재 `search.py` 파일은 없다. 별도 검색기 통합 모듈이 필요하면 후속 리팩터링으로 추가할 수 있으나, 다음 필수 노드는 아니다.
- N-04 `draft_answer.py`는 근거 묶음으로 답변 초안을 만든다. N-03 `verify_grounding.py`는 그 초안의 주장과 근거를 검사하므로 N-04 다음에 연결한다.

## 함수형 호출

```python
from rag.contracts import ContextExpansionConfig
from rag.nodes.get_source_context import get_source_context

# retrieval_result는 N-01의 NodeResult[RetrievalCandidates]이다.
# 실패한 N-01을 실행할지 여부는 호출자/하네스가 먼저 결정한다.
result = get_source_context(
    retrieval=retrieval_result.data,
    chunk_lookup=chunk_lookup,
    block_lookup=block_lookup,
    document_scope=request.document_scope,
    config=ContextExpansionConfig(
        before_chars=200,
        after_chars=200,
        max_total_chars=12000,
    ),
)
```

노드 함수는 파일을 다시 읽거나 출력하지 않고 `NodeResult[EvidenceBundle]`을 반환한다. 하네스가 전달한 허용 문서·버전을 필수로 받는다. 현재 `DocumentRef`는 문서·버전 단위이며 페이지별 권한을 표현하는 타입은 아니다.

200/200/12000은 이번 개발 기본값이다. 팀 전체의 최종 길이·예산 값으로 확정한 수치는 아니다. 토큰 수나 UTF-8 바이트 수가 아닌 **Unicode 문자 수**이다.

## 처리 방법

1. 모든 후보의 문서·버전이 허용 범위 안인지 먼저 확인한다.
2. 후보 ID로 청크를 조회하고 원본 블록의 문서·버전·페이지·좌표·문자 구간을 대조한다.
3. 원본 블록 구간을 줄바꿈으로 연결했을 때 청크 `text`와 일치하는지 확인한다.
4. 같은 문서·버전·페이지의 저장된 블록 순서로 앞뒤 문맥을 선택한다. `block_id`나 `block_index`로 재정렬하지 않는다.
5. 같은 페이지에서 겹치거나 맞닿는 문맥 창은 합쳐 텍스트 중복을 줄인다. 병합 전의 검색 청크 ID·순위·원문 구간은 `anchors`에 남긴다.
6. 근거 묶음의 `text` 총합에 문자 상한을 적용한다. 초과하면 추가 문맥을 생략하고 후보 원문 전체를 우선 보존한다. 원문 자체도 담을 수 없으면 해당 후보를 제외하고 진단한다. 원문 일부를 잘라 놓고 전체가 복원됐다고 표시하지 않는다.

블록 내부 텍스트는 바꾸지 않으며 블록 사이에는 줄바꿈 하나를 사용한다. 원문 위치는 기존 블록 bbox를 유지하고, 일부 문자에 해당하는 정확한 bbox를 새로 추정하지 않는다.

여러 페이지 청크를 입력하면 페이지별 근거 항목으로 나누되 같은 `chunk_id`를 앵커로 유지한다. 추가 문맥을 다른 페이지로 확장하지 않는다. 전체 페이지가 이미 한 청크인 경우 문맥을 추가하지 않아도 정상이다.

## 반환 데이터

공통 바깥 구조는 `status`, `data`, `diagnostics`, `outcome` 네 필드이다.

| 타입 | 핵심 필드와 의미 |
| --- | --- |
| `ContextExpansionConfig` | 앞·뒤 문자 수, 전체 근거 텍스트 문자 상한 |
| `SourceSpan` | `block_id`, `pdf_page`, `start_char`, `end_char`, 원문 `text`, `source_locator`, 추출 방법·검증 상태 |
| `EvidenceAnchor` | 원래 검색된 `chunk_id`, `rank`, 해당 페이지의 원문 텍스트·구간 |
| `EvidenceItem` | `evidence_id`, 문서·버전, 페이지, 보강한 `text`, 구간 목록, `anchors`, 추출 검증 상태 |
| `EvidenceBundle` | 요청 참조, `items`, 설정·문맥 전략 버전, 총 문자 수, 요청/해결 후보 수, 제외 후보 ID, 예산 제한 여부 |

`items`는 병합된 문맥 단위이다. 따라서 검색 후보 수와 근거 항목 수가 항상 같지는 않다. `char_count`는 병합된 근거 본문 `items[*].text`의 총 문자 수이며, JSON 메타데이터·앵커의 중복 저장량이나 LLM 토큰 수는 아니다.

`verification_status`는 원문 블록 상태를 전달하며 여러 상태가 섞이면 `mixed`이다. N-02가 원문이나 답변의 사실성을 검증해 `verified`로 바꾸는 것은 아니다. 이 스키마는 현재 JM의 개발 계약이며 최종 Parser/VLM·하네스 통합 시 상호 합의가 필요하다.

## 상태와 오류 정책

| 상황 | 반환 |
| --- | --- |
| 정상 조회·복원·문맥 구성 | `success`, 근거 묶음 |
| 선택적 문맥 확장만 문자 예산 때문에 생략 | `success`, 원래 후보 원문 보존, `budget_limited=true`, 생략 진단 |
| 일부 청크/블록 누락 또는 예산으로 일부 후보 제외 | `partial`, 확보한 근거와 제외 사유 |
| 모든 후보 원문을 확보하지 못함 | `error`, `data=null` |
| 허용 범위·문서 버전 불일치, 원문/좌표/구간 무결성 오류 | `error`, `data=null`. 앞서 얻은 원문도 반환하지 않음 |
| 정상적인 빈 후보 입력 | `success`, 빈 `items`, `outcome="no_candidates"` |

N-01의 `partial` 응답은 CLI에서 읽을 수 있으나 N-02의 상태는 자신의 처리 결과를 나타낸다. 전체 워크플로에서는 N-01의 실패 진단도 별도로 유지해야 한다.

## CLI 실행

저장소 루트에서 BM25를 실행하던 Python 환경을 사용한다. 다음은 실제 데이터 경로 예시이다.

```bash
mkdir -p outputs
python -m rag.nodes.retrieve_evidence \
  --chunks data/processed/rag/kistep_2025_plan_0530d100e085/3382f591c928/chunks.jsonl \
  --query "조달재원 출연금" --top-k 3 > outputs/n01_context_demo.json

python -m rag.nodes.get_source_context \
  --chunks data/processed/rag/kistep_2025_plan_0530d100e085/3382f591c928/chunks.jsonl \
  --blocks data/processed/pymupdf_demo/kistep_2025_plan_0530d100e085/blocks.jsonl \
  --retrieval-result outputs/n01_context_demo.json \
  --before-chars 200 --after-chars 200 --max-total-chars 12000 \
  > outputs/n02_context_demo.json
```

`outputs` 폴더가 없다면 먼저 생성한다. CLI는 이제 미리보기 로그 대신 공통 결과 JSON만 stdout에 출력한다. 결과가 `error`이면 종료 코드는 2이다. `--retrieval-result` 대신 `--chunk-id` 하나를 지정해 단일 청크로도 확인할 수 있다. `--scope DOCUMENT_ID VERSION`을 반복 지정하면 허용 범위를 명시할 수 있다. 생략하면 로컬 청크 파일의 문서·버전 전체를 사용하며, 이는 사용자별 권한 검증을 대신하지 않는다.

## 웹 연결

기존 웹 실행 명령을 그대로 사용할 수 있다. 선택적으로 서버의 `--before-chars`, `--after-chars`, `--max-total-chars`로 설정을 변경할 수 있다.

- `추출 텍스트`에는 N-02가 반환한 보강 문맥과 원본 구간을 표시한다.
- `상세 정보`에서는 원문/보강 후 문자 수, 병합된 근거 ID, N-01 JSON과 N-02 `EvidenceBundle` JSON을 확인한다.
- 다운로드 JSON의 `source_context`가 N-02 공통 결과이다. 기존 화면 호환용 `sources`는 이를 후보별로 펼친 뷰이다.
- PDF 초록 표시는 기존 검색 청크의 참조 블록이며, 추가 문맥의 문자 단위 위치를 새로 그리는 기능은 아니다.

## 검증

2026-11-02 실제 KISTEP 입력 180개 청크가 모두 원문과 연결됐고, 전체 후보를 함께 처리하면 겹치는 문맥이 115개 페이지별 근거·102,884자로 병합됐다. 전체 후보 검사는 상한을 500,000자로 높여 수행했다. 기본 상한은 12,000자이다.

`조달재원 출연금` Top K=3은 PDF 109·110·105페이지 순서를 유지한다. 109페이지는 21블록 전체가 한 청크여서 확장이 없다. PDF 10페이지의 첫 청크는 기본 설정에서 515자에서 715자로 보강된다.

```bash
python -m unittest discover -s tests -p 'test_get_source_context.py'
```

확장·병합·페이지 경계·출처 복원·범위/버전 오류·문자 예산·부분 실패·CLI 계약을 검증한다. 이는 검색 정확도, OCR/VLM 인식 정확도, 생성 답변의 사실성 평가가 아니다.
