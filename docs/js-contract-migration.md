# JM 공통 계약 변경 및 현재 실행 방법 — 2026-11-05

JS 설계 `01fd23f884c9e2a4ce1cf79fe2b15c97576aaca0`을 현재 PyMuPDF 텍스트 데모에 연결한 구현이다. 기존 파싱·청킹 결과를 재사용한다. 2026-11-07에는 N-03 단독 검증 노드와 CLI를 추가하였다. T-01의 LangGraph 연결·1회 보정·재검증도 구현하였다. 최종 Parser/VLM 공통 입력 어댑터, Dense/RRF와 로컬 웹 연결은 구현하였고 메인 하네스 연동은 별도이다.

## 현재 노드 계약

| 노드 | 입력 | 출력 |
| --- | --- | --- |
| N-01 | RetrievalRequest, BM25Retriever | NodeResult[RetrievalCandidates] |
| N-02 | 검색 후보, 청크·블록 조회표, 허용 문서·버전, 문맥 설정 | NodeResult[EvidenceBundle] |
| N-04 | request_ref, 원래 질문, EvidenceBundle, 선택형 revision_targets | NodeResult[ReviewDraft] |
| N-03 | request_ref, ReviewDraft, EvidenceBundle | NodeResult[VerificationReport] |

### N-01

- 허용된 문서·버전을 확인하고 범위에 해당하는 후보 중 Top-k를 반환한다. 현재 BM25 기준선은 전체 후보를 정렬한 뒤 범위를 필터링하고 최종 Top-k를 적용한다.
- 정상 검색의 후보가 0개이면 success/no_candidates다. 입력·색인·검색 오류는 error이며 자료 부재로 해석하지 않는다.
- 호출자가 index_ref를 지정하지 않으면 색인 입력과 설정으로 런타임 해시 참조를 만든다. 저장 색인 Manifest 통합은 별도 작업이다.

### N-02

- EvidenceBundle은 request_ref, evidence_items, context_profile_ref를 갖는다.
- 각 EvidenceItem은 chunk_id, document_id, document_version, retrieval_rank, context_text, source_refs, context_truncated를 갖는다.
- 후보 하나당 근거 하나다. 문맥이 겹쳐도 병합하지 않고 동일 chunk_id만 중복 제거한다.
- source_refs는 검색된 원문 구간을 가리킨다. context_text 전체를 동일한 인용 좌표로 취급하지 않는다. 원문 구간의 오프셋은 시작 포함·끝 제외다.
- 일부 조회 실패는 partial이며 diagnostics의 failed_items에 실패 chunk_id를 기록한다. 무결성 위반은 전체 error다.
- 출력 상한 제외는 실행 실패가 아니다. 낮은 순위 후보를 제외하고 diagnostics.context_budget.excluded_refs에 기록한다. excluded_refs는 이번 구현에서 선택한 진단 필드명이다.
- 원문 경계나 예산으로 문맥이 줄면 context_truncated로 표시한다. 문맥을 일부 줄여도 원래 검색 구간은 자르지 않는다.

### N-04

```python
node.run(request_ref, question, evidence_bundle)
node.run(request_ref, question, evidence_bundle, revision_targets=targets)
```

ReviewDraft는 request_ref, claims, unanswered_items를 갖는다. 각 주장은 claim_id, claim_text, evidence_refs를 가지며 evidence_refs 값은 chunk_id다. 최초 생성의 claim_id는 코드가 부여한다.

- 일부만 답변 가능하면 success/partial_answer다. 생성할 주장이 없으면 success/insufficient_evidence다.
- 생성된 모든 주장의 근거 참조가 비어 있거나, 존재하지 않는 참조가 하나라도 있으면 error다.
- 일부 주장만 근거 참조가 없으면 초안에 남을 수 있다. 검증 완료를 뜻하지 않는다.
- 보정 입력 RevisionTarget은 claim_id, claim_text, reason, evidence_refs다. 기존 ID를 유지해 고친 주장만 반환한다. 새 ID·대상 밖 ID·빈/잘못된 참조는 error다.
- 보정 대상이 비었거나 근거가 없는 정상 생성 입력이면 모델을 호출하지 않는다. 보정 1회 제한과 수정 주장만의 N-03 재호출은 T-01이 관리한다.
- JSON·근거 참조가 유효해도 의미적 사실성이 확인된 것은 아니다. N-03의 별도 모델 판정은 [실행 가이드](n03-grounding.md)를 참고한다. 실제 판정 품질 평가는 별도이다.

### N-03

주장별 독립 메시지로 판정한다. 무참조 주장은 건너뛰고 필수 처리 실패는 주장별로 기록한다. 정상 판정은 verdict와 관계없이 처리 완료이며 일부 실행 실패는 partial, 전체 실패는 error이다. T-01 보정 순환과 로컬 웹 실행을 연결하였다. [계약·실행 방법·한계](n03-grounding.md)를 참고한다.

## 기존 데이터 파일

원본 PDF, blocks.jsonl, chunks.jsonl은 그대로 사용한다. 이전 items/evidence_id 형식의 N-02 결과 파일은 새 로더와 호환되지 않으므로 N-02 결과 JSON을 새 파일명으로 다시 생성한다.

프로젝트 루트에서 다음을 실행한다. Python에는 Kiwi와 rank-bm25가 설치되어 있어야 한다.

```bash
python -m rag.nodes.retrieve_evidence --chunks "data/processed/rag/kistep_2025_plan_0530d100e085/3382f591c928/chunks.jsonl" --query "조달재원 출연금" --top-k 3 > outputs/n01_js_demo.json
python -m rag.nodes.get_source_context --chunks "data/processed/rag/kistep_2025_plan_0530d100e085/3382f591c928/chunks.jsonl" --blocks "data/processed/pymupdf_demo/kistep_2025_plan_0530d100e085/blocks.jsonl" --retrieval-result outputs/n01_js_demo.json > outputs/n02_js_demo.json
```

outputs 폴더가 없다면 먼저 생성한다. 로컬 웹 데모 실행 옵션은 기존과 동일하다. N-04 JSON 로더와 화면은 새 필드명에 맞췄다. 근거 버튼은 검색 원문 미리보기이며 모델이 직접 인용한 문자열을 보증하지 않는다.

## 확인 결과와 한계

- 관련 테스트 91개 통과: 토큰 검색, 계약, 원문 복원, CLI, 생성·보정의 모의 응답, 웹 서버 연결을 포함한다.
- 실제 KISTEP 블록 3,105개와 청크 180개로 원문 근거 180개를 구성했다. 전체 후보 확인의 문자 예산은 500,000자로 높였다. 기본 예산은 12,000자다.
- 기본 상위 3개 결과는 PDF 109·110·105페이지이며 문맥 합계는 2,179자다. 새 JSON 저장·로드를 확인했다.
- 브라우저 검사 22개 통과: PDF 표시, 근거 이동, 새 계약 표시·내보내기, 빈 검색, 모바일 레이아웃을 포함한다.
- 실제 LLM 호출은 0회다. 브라우저 생성 응답은 테스트용 모의 데이터로 명시했다. 위 검사는 검색 정확도, 생성 품질, OCR/VLM 정확도, N-03 의미 검증 성능을 증명하지 않는다.

예전 n02-source-context.md, n04-draft-answer.md, rag-demo-ui.md와 retrieval/README.md는 이전 구현 설명과 검증 기록으로 보존한다. 현재 API에는 이 문서를 우선 적용한다.

## T-01 통합 — 2026-11-07

JS ad430ab 기준으로 네 Small Node와 모순 주장만의 1회 보정·재검증을 LangGraph로 연결하였다. 현재 검색은 BM25 기준선이다. WorkflowResult에 확인·미확인 항목과 출처·실행 상태를 분리하며 사용자용 최종 문장 생성은 포함하지 않는다. [입출력·상태·실행 가이드](t01-document-qa.md)를 참고한다. 위의 91개 테스트와 브라우저 검사 수치는 이전 변경의 기록이며 이번 통합 품질 평가 수치가 아니다.

## 로컬 자료함·T-01 웹 — 2026-11-07

PDF 업로드·자료 이름·선택 범위·제외/복원과 T-01 비동기 실행을 연결하였다. 기존 단일 문서 N-04 데모와 최종 검증 결과를 구분한다. [현재 웹 실행 방법](web-workspace.md)을 우선 참고한다.
