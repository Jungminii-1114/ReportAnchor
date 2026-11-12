# T-02 · Criteria Review — 기준 충족 검토

기준 설계는 JS `ad430ab123c61332e099765ab3e1a21c81c8dc7e`의 [T-02](https://github.com/Jungminii-1114/ReportAnchor/blob/ad430ab123c61332e099765ab3e1a21c81c8dc7e/docs/big-nodes/T-02.md), [N-05](https://github.com/Jungminii-1114/ReportAnchor/blob/ad430ab123c61332e099765ab3e1a21c81c8dc7e/docs/small-nodes/N-05.md), [N-06](https://github.com/Jungminii-1114/ReportAnchor/blob/ad430ab123c61332e099765ab3e1a21c81c8dc7e/docs/small-nodes/N-06.md)를 따른다. 아래 실제 필드와 API는 JM 구현에서 구체화한 계약이다. JS 문서를 수정하거나 병합한 것은 아니다.

## 구현 범위

| 파일 | 역할 |
| --- | --- |
| `rag/nodes/structure_criteria.py` | N-05. 명시된 독립 기준을 조건으로 분리한다. 정상적인 비어 있지 않은 입력에 LLM 1회, Python 구조 검사이다. |
| `rag/nodes/evaluate_criterion.py` | N-06. 기준 하나와 원문 근거로 조건별 판정 초안을 만든다. 직접 근거와 인용을 요구한다. |
| `rag/workflows/criteria_review.py` | T-02. 기준별 검색·문맥·평가, N-03 검증, 검증 후 AND/OR 집계 및 CLI이다. |
| `rag/nodes/_criteria_support.py` | JSON/입력 검사와 3값 논리 등 내부 보조 함수이다. 별도 Small Node가 아니다. |
| `rag/llm/response_schemas.py` | 로컬 Ollama에 전달할 노드별 응답 스키마이다. |
| `rag/contracts.py` | 원문 기준·조건·미처리 기준·제안 판정·검증 후 결과 타입이다. |

```text
CriteriaReviewTask
  └─ N-05: CriteriaSet
       ├─ 미처리 기준 원문/사유 보존
       └─ 처리 가능한 기준마다
            N-01: 기준 원문으로 후보 검색
              → N-02: 허용 문서/버전의 원문 문맥 확보
              → N-06: 조건별 판정 제안 + 정확한 원문 인용
       → N-03: 각 기준의 원래 근거로 판정과 조건의 연결 검증
       → 조건별 검증 결과를 반영한 all/any 집계
       → CriteriaReviewResult
```

N-01/02/03은 기존 구현을 재사용한다. N-03 입력은 기존 ReviewDraft로 어댑트하며 사용자 조건, 제안한 충족/미충족 결론, 근거 사실을 함께 검증한다. 근거 사실만 맞고 조건 결론이 틀린 경우를 놓치지 않도록 한다. 기준별 같은 chunk_id가 서로 다른 길이의 문맥으로 반환되어도 합치지 않는다. 원래 EvidenceBundle별로 검증하므로 다른 기준의 근거가 섞이지 않는다.

## 입력과 구조화 원칙

`CriteriaReviewTask`는 `request_ref`, `question`, `criteria_texts`, `document_scope`, `retrieval_profile`, `context_profile`을 가진다. 검색 입력은 원래 질문에서 새로 생성한 질의가 아니라 **각 독립 기준의 원문**이다. 권한을 확인한 문서 ID·버전 범위를 상위 계층에서 전달한다.

- 독립 기준은 `criteria_texts`의 서로 다른 항목으로 준다. 하나의 기준 안에서 복합 조건을 분리하는 것은 N-05의 책임이다.
- 평면 `all`(모두) / `any`(중 하나)만 지원한다. 기준 하나의 중첩 AND/OR를 일부만 발췌하지 않는다.
- 원문은 Python이 입력에서 그대로 복사한다. `criterion_id`, `condition_id`, `claim_id`도 코드가 발급한다. N-03이 다시 발급하지 않는다.
- 입력 기준은 처리/미처리 중 정확히 한 곳에 대응해야 한다. 누락·중복 ID·잘못된 JSON은 실행 오류이다.
- `source_quote`는 사용자 기준 원문에서 가져온 문자열이다. **문서 근거 인용과는 다르다.** N-06에는 코드가 분할한 원문 `passages`와 `quote_id`를 제공한다. 모델은 `quote_ids`만 선택하고 코드가 실제 원문과 chunk_id를 `quotes`/`evidence_refs`에 넣는다. 모델이 인용문을 재작성하지 않는다. 각 인용의 `context_start`/`context_end`는 N-02 context_text에서 시작 포함·끝 제외의 문자 위치이다. source_refs는 기존 검색 블록 위치이며 확대 문맥 전체나 인용문만의 PDF 좌표를 뜻하지 않는다.
- 수치 문자열의 누락·창작을 보수적으로 검사한다. 이 검사는 조건의 전체 의미 보존을 보증하지 않는다. 구조화된 조건은 실행 기록에서 확인할 수 있다.
- 실행 상한은 독립 기준 20개, 기준 원문당 4,000자, 기준당 조건 8개이다. 이 상한은 하네스 전체 토큰·비용 예산 관리와 별개이다.

## 판정과 실행 상태

| 값 | 의미 |
| --- | --- |
| `met` | 연결된 직접 근거가 조건 충족을 지지하며 N-03도 supported인 최종 조건 판정이다. |
| `not_met` | 직접적인 위반 근거가 있고 N-03도 supported인 최종 조건 판정이다. 실행 오류가 아니다. |
| `undetermined` | 자료 부족·불명확·근거 검증 미통과 등으로 확인하지 못한 것이다. |

N-06의 met/not_met는 **아직 제안**이며 최종 결과는 T-02를 사용한다. 제안 판정은 `proposed_verdict`, 검증 후 판정은 `verdict`로 구분한다. N-03이 contradicted/insufficient이거나 실패하면 해당 조건을 undetermined로 낮추며 `verified_claim_text`에 확인되지 않은 문장을 넣지 않는다. 검증 실패는 `errors`에도 실행 오류로 남긴다. 원문 참조·문서 버전·페이지·좌표는 `sources`에 유지한다.

- Top-k 검색에서 못 찾았다는 이유로 not_met로 만들지 않는다. 현재 문서 전체 부재를 입증하는 완전성 검사는 없다.
- AND: 하나라도 not_met면 not_met, 모두 met면 met, 나머지는 undetermined이다.
- OR: 하나라도 met면 met, 모두 not_met면 not_met, 나머지는 undetermined이다.
- **전체 기준의 합격/불합격·총점은 계산하지 않는다.** 사용자 정의 집계 규칙이 없기 때문이다.
- `status`는 실행 상태이다. 일부 필수 실행 실패와 안전한 결과가 함께 있으면 partial, 모두 실패했으면 error이다. 부족한 근거 자체는 success/undetermined이다.
- N-05가 일부 기준만 지원하면 success/partial_criteria이다. 전부 미지원이면 success/unsupported_structure, 모호함이 섞이거나 기준이 없으면 success/needs_clarification이다. 기준 0건이면 검색·평가·검증을 호출하지 않는다.
- 자동 재검색, N-06 판정 보정, 무제한 반복은 없다. N-05 1회 + 처리 기준당 N-06 최대 1회 + 결정 가능한 조건당 N-03 최대 1회이다.

## 실행

기존 문서 추출·청킹 결과를 그대로 사용한다. PDF를 다시 파싱하지 않는다. Ollama 서버가 실행 중이고 모델이 설치되어 있어야 실제 판정이 가능하다.

프로젝트 루트에서 다음을 실행한다. 새 폴더 생성이나 재설치는 필요하지 않다. 다른 PDF를 사용한다면 chunks/blocks 및 문서 ID·버전을 해당 데이터에 맞게 바꾼다.

```bash
python -m rag.workflows.criteria_review \
  --chunks "data/processed/rag/kistep_2025_plan_0530d100e085/3382f591c928/chunks.jsonl" \
  --blocks "data/processed/pymupdf_demo/kistep_2025_plan_0530d100e085/blocks.jsonl" \
  --scope "doc_0530d100e08517b6" "0530d100e08517b60bb2acb9504d7ead70107f0c23a586738fb37e2191d1add8" \
  --request-ref "criteria-demo-001" \
  --criterion "2025년 경상운영비는 4,000백만원 이상이어야 한다." \
  --criterion "2025년 경상운영비는 5,000백만원 이상이어야 한다." \
  --model "qwen3:8b"
```

`--criterion`은 독립 기준마다 반복한다. 여러 줄 기준은 문자열 목록 JSON을 작성하고 `--criteria-file 파일.json`으로 전달할 수 있다. 두 입력 방식은 함께 사용하지 않는다. `--trace-out outputs/t02-trace.json`을 명시하면 원문 포함 노드별 기록을 저장한다. 해당 출력의 부모 폴더는 미리 존재해야 한다. 기본 실행은 결과 JSON만 표준 출력에 기록하며 모의 모델로 대체하지 않는다.

Python 호출은 다음과 같다.

```python
from rag.contracts import CriteriaReviewTask
from rag.workflows.criteria_review import CriteriaReviewWorkflow

workflow = CriteriaReviewWorkflow(retriever, chunk_lookup, block_lookup, client)
result = workflow.run(CriteriaReviewTask(
    request_ref="review-001",
    question="명시한 조건을 검토해줘.",
    criteria_texts=["2025년 경상운영비는 4,000백만원 이상이어야 한다."],
    document_scope=authorized_documents,
))
```

N-05만 따로 실행할 때는 `python -m rag.nodes.structure_criteria --request-ref demo --criterion "사용자 기준" --model qwen3:8b`를 사용한다. N-06은 `EvaluateCriterionNode(client).run(request_ref, criterion, evidence_bundle)`로 호출한다.

## 로컬 모델 컨텍스트

공통 OllamaClient는 로컬 호출에 `options.num_ctx=16384`를 명시한다. T-02 CLI의 `--num-ctx` 또는 Python 생성자의 `num_ctx`로 조정할 수 있다. Cloud에는 이 로컬 옵션을 보내지 않는다. 큰 컨텍스트는 더 많은 메모리를 사용하며, 입력이 설정을 넘지 않도록 검색 Top-k·문맥 예산도 함께 관리해야 한다. 문자 예산은 토큰 예산과 다르다.

실제 점검 중 기본 서버가 입력 5,272토큰을 2,050토큰으로 잘라 처리하는 로그가 확인되었다. 이 상태에서는 구조화 출력이 유효해도 조건·시스템 규칙이 모델에 온전히 전달되지 않을 수 있다. JSON 스키마 검사가 입력 잘림을 탐지하는 것은 아니다. 컨텍스트 설정과 서버 로그를 함께 확인해야 한다. 설정의 의미는 [Ollama 컨텍스트 문서](https://docs.ollama.com/context-length)를 참고한다.

## 현재 경계와 검증

T-02는 Python/CLI tool이다. 기존 웹의 질의·검증 모드는 T-01이며 T-02를 자동 호출하지 않는다. 웹 검토 기준 입력·라우팅, N-10/N-11, 메인 하네스 기록·예산 연동은 별도이다. 실제 하네스 담당자에게 전달할 입력과 반환 타입은 위 계약을 사용한다. 기존 API와 T-01 타입은 변경하지 않는다.

정확한 인용 문자열·ID가 존재한다는 사실은 의미적 판정의 정확성을 보증하지 않는다. 같은 모델의 생성과 검증은 오류가 상관될 수 있다. 기준 분해와 실제 판정에는 사람 정답이 있는 별도 평가가 필요하다. 검색은 BM25 기준선이며 OCR/VLM/표 구조 처리 성능을 검증한 결과가 아니다.

```bash
python -B -m unittest tests.test_criteria_nodes tests.test_criteria_review
```

자동 테스트는 모델 응답을 모의로 제공하고 JSON 스키마·논리 집계·출처·실행 실패를 확인한다. 공개 KISTEP 실제 blocks/chunks 연결 검사와 실제 로컬 모델 사례는 모델 모의 응답 테스트와 구분해서 기록한다. 일반 실행 코드에는 모의 응답이 없다.

## 2026-11-12 확인 결과

- 자동 회귀 검사: RAG·웹 267개, 별도 PyMuPDF 환경의 PDF 검사 9개 통과이다.
- 공개 KISTEP blocks 3,105개·chunks 180개를 실제로 검색·조회하고, 모의 모델 응답으로 충족·미충족·확인 불가 흐름을 구분하였다.
- 실제 로컬 `qwen3:8b`에서는 두 독립 기준을 N-05부터 N-03까지 새 호출 5회로 실행하였다. 2025년 경상운영비 4,566백만원에 대해 ‘4,000백만원 이상’은 met, ‘5,000백만원 이상’은 not_met이며 두 판정 모두 N-03 supported였다.
- 컨텍스트 16,384 설정이 실제 서버에 적용되었고 마지막 실행 로그에 입력 잘림 경고는 없었다. 두 기준의 전체 실행은 약 172초였다. 장치·모델·조건 수에 따라 지연은 달라진다.
- 이는 연결 및 두 사례의 정합성 확인이다. 일반적인 판정 정확도나 통계적 성능 평가 수치가 아니다.

[검증 기록 JSON](reports/t02-validation.json)에 모의 검사·실제 모델 검사·개발 중 실패와 수정·한계를 분리해 남겼다. 원시 모델 응답과 전체 실행 로그는 공개 문서에 포함하지 않는다.
