# Query Agent MVP — Small Node 설계 LIST

> 상태: **Small → Big → Small 최상위 구조 확정(2026-10-31), Small Node 11종 등록 / 구현 미착수**. 이 문서는 Small Node의 이름·책임·최소 입출력을 나열하는 **LIST**다. **N-01~N-11 공통 반환**은 [NodeResult[T] 계약](small-nodes/common-result-contract.md)으로 고정하고, 아래 표의 출력은 `data`의 업무 산출물(또는 업무상 `outcome`)을 가리킨다. 업무별 실행 순서·분기는 [Big Node 상세 문서](big-nodes/README.md), 용어는 [명칭 통일 문서](naming-conventions.md)를 기준으로 한다.
>
> 범위: 최상위 공통 Small Node N-10·N-11, Big Node 내부 Small Node N-01~N-09. MVP Big Node 3종(T-01~T-03)에 우선 적용하며 T-04·T-05는 후속 계획으로 유지한다.

## 1. 최상위 공통 Small Node

N-10과 N-11은 메인 에이전트의 역할이다. 메인 에이전트는 Big Node를 tool로 호출하고, 그 내부의 N-01~N-09는 직접 호출하지 않는다.

| ID / 상세 문서 | 표준 이름 | 단일 책임 | 최소 입력 → 출력 |
|---|---|---|---|
| **[N-10](small-nodes/N-10.md)** | `route_query` | 요청·권한·범위 확인과 작업 선택 | 원본 요청·허용 범위 → `RoutedTask 또는 실행 전 종료 사유` |
| **[N-11](small-nodes/N-11.md)** | `deliver_result` | 검증된 결과·출처·상태 전달 | WorkflowResult 또는 실행 전 종료 사유 → `사용자 결과` |

## 2. 내부 공통 Small Node

| ID / 상세 문서 | 표준 이름 | 단일 책임 | 최소 입력 → 출력 |
|---|---|---|---|
| **[N-01](small-nodes/N-01.md)** | `retrieve_evidence` | 검색 후보 탐색 | 검색 텍스트·문서/버전 범위·검색 설정 → `RetrievalCandidates` |
| **[N-02](small-nodes/N-02.md)** | `get_source_context` | 후보와 원문·표·헤더·각주·위치 연결 | 후보 참조·문서 버전 → `EvidenceBundle` |
| **[N-03](small-nodes/N-03.md)** | `verify_grounding` | 주장·판정·비교 결과와 원문 근거의 정합성 검사 | 결과 초안·EvidenceBundle → `VerificationReport` |

## 3. 내부 전용 Small Node

| ID / 상세 문서 | 표준 이름 | 단일 책임 | 최소 입력 → 출력 |
|---|---|---|---|
| **[N-04](small-nodes/N-04.md)** | `draft_answer` | 원문 근거를 이용한 질문 답변 초안 작성 | 질문·EvidenceBundle → `ReviewDraft` |
| **[N-05](small-nodes/N-05.md)** | `structure_criteria` | 명시된 검토 기준을 판정 항목으로 구조화 | 사용자 기준 원문 → `CriteriaSet` |
| **[N-06](small-nodes/N-06.md)** | `evaluate_criterion` | 기준별 충족·미충족·확인 불가 판정 | CriteriaSet 항목·EvidenceBundle → `CriterionFinding` |
| **[N-07](small-nodes/N-07.md)** | `define_comparison` | 문서·기간·지표·범위 및 비교 축 정의 | 비교 요청·허용 문서 범위 → `ComparisonSpec` |
| **[N-08](small-nodes/N-08.md)** | `check_comparability` | 기간·단위·집계 조건과 정성 비교 범위 검사 | ComparisonSpec·대상별 EvidenceBundle → `비교 가능성·사유(타입 미정)` |
| **[N-09](small-nodes/N-09.md)** | `build_comparison` | 정량 계산 또는 정성 비교 결과 구성 | 비교 가능성·대상별 확인된 값/내용 → `ComparisonFinding` |

## 4. Small Node로 분리하지 않는 내부 기능

| 기능 | 위치·이유 |
|---|---|
| Dense 검색·BM25·RRF·선택형 Reranker | **N-01 내부 검색 파이프라인**. 세부 구성은 [문서 처리 파이프라인](document-pipeline.md)에서 관리 |
| 표의 헤더·각주·주변 문맥 확장 | **N-02 내부 원문 조회 기능** |
| 결정적 산술·단위 환산 | **N-06 또는 N-09에서 이용하는 공통 계산 기능**. 독립 그래프 노드 여부는 구현 시 판단 |
| 요청 분류·Big Node 선택 | **N-10 `route_query`** 책임. Big Node 내부에서 다시 수행하지 않음 |
| 사용자용 결과 서식화 | **N-11 `deliver_result`** 책임. Big Node 내부에서 별도 공통 노드로 중복 정의하지 않음 |
| 체크포인트·Trace·오류·예산 제어 | **하네스 공통 실행 기능**. 업무 목적 노드와 구별 |

**설계 원칙:** 목록의 Small Node가 반드시 별도 LLM 호출이나 별도 LangGraph 노드 하나를 의미하는 것은 아니다. 구현 시 독립 실행·테스트·재사용이 필요한 경계만 노드로 유지하고 단순 연산은 내부 함수로 둘 수 있다. **새 쿼리를 만드는 증폭 기법은 H-12 백로그 유지**이며, N-05의 명시된 기준 구조화는 그와 다르다.

**관련 문서:** [Big Node 상세 문서](big-nodes/README.md) · [Small Node 상세 문서](small-nodes/README.md) · [Big Node / Small Node 명칭 통일](naming-conventions.md) · [Big Node MVP 워크플로](query-agent-mvp.md) · [문서 처리·하이브리드 검색 파이프라인](document-pipeline.md) · [하네스 아이디어 백로그](idea-backlog.md)
