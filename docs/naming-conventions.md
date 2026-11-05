# ReportAnchor — MVP 명칭 통일 기준

> **상태:** **MVP 최상위 Small → Big → Small 구조 확정(2026-10-31)**. Big Node 5종 계획(우선 3종), Small Node 11종 등록. 실제 코드·데이터 스키마·실행 설정은 미확정이다.
>
> **목적:** 목적별 고정 워크플로는 **Big Node**, 입구·출구와 Big Node 안에서 쓰는 최소 책임 단위는 **Small Node**로 통일한다.
>
> **범위:** 현재 ReportAnchor에서 새로 설계한 MVP 구조와 이름. 과거 프로젝트·레거시 코드에서 가져온 패키지·파일 경로·데이터셋 전용 타입·실행 설정 등은 포함하지 않는다.

## 1. 핵심 계층: Big Node와 Small Node

| 표준 명칭 | 기존 문서에서의 표현 | 의미 | 식별자 |
|---|---|---|---|
| **Big Node** | 작업 Tool, Task Tool, 목적별 고정 워크플로 | **사용자 목적 하나를 해결하는 전체 워크플로**. 메인 에이전트가 tool로 호출하고 내부에서 Small Node를 정해진 순서·분기로 실행 | `T-01`부터 `T-05` |
| **Small Node** | 최소 단위 노드, 공통/전용 노드 | **입출력이 구분되는 최소 책임 단위**. Big Node 내부에서 사용하거나, 상위 실행 경로의 입구·출구에서 사용함 | `N-01`부터 `N-11` |
| **내부 기능** | 기초 기능, 함수·모듈 | Small Node 내부의 세부 연산. Dense/BM25/RRF, 원문 위치 조회 보조, 결정적 계산 등. **별도의 노드 계층으로 세지 않음** | ID 미부여 |

**명칭 선택:** 문서의 대표 표현은 **Big Node / Small Node**로 통일한다. **Tiny Node는 사용하지 않는다.** 구체적인 함수나 연산을 뜻할 때는 *내부 기능*이라고 부른다.

**중요:** Big Node가 반드시 LangGraph의 단일 노드 1개를 의미하지는 않는다. MVP에서 Big Node는 **고정 서브워크플로 전체**이며, Small Node도 구현 시 반드시 개별 LangGraph 노드 또는 LLM 호출 1회가 되는 것은 아니다.

```text
ReportAnchor Query Agent — MVP 확정 실행 경로
  1. Small Node: N-10 route_query       (메인 에이전트: 질문 확인·tool 선택)
           ↓
  2. Big Node: T-01 / T-02 / T-03     (메인 에이전트의 tool, 요청당 하나만 호출)
       └─ Small Node: N-01 ~ N-09 중 업무별 조합
           └─ 내부 기능: 검색 엔진 연산·계산 등
           ↓
  3. Small Node: N-11 deliver_result   (메인 에이전트: 검증 상태·결과 전달)

  ※ 실행 전 종료: N-10 → N-11 (명확화·권한 거부·미지원)
```

**확정 범위:** Small → Big → Small의 최상위 실행 구조, 요청당 하나의 Big Node 선택, Big Node 내부의 사전 정의된 Small Node 실행 순서·분기를 MVP 기준선으로 고정한다. 재계획·Big Node 연쇄 호출·자유 반복은 기본 경로에서 제외한다. 추후 실험 결과에 따라 변경 가능하다.

## 2. Big Node 목록 (MVP 우선 3종 + 계획 2종)

| ID | 표준 명칭 | 한국어 명칭 | 현재 범위 |
|---|---|---|---|
| **[T-01](big-nodes/T-01.md)** | **Document QA** | 문서 근거 질의응답 | MVP 상세 워크플로 설계 |
| **[T-02](big-nodes/T-02.md)** | **Criteria Review** | 기준 충족 검토 | MVP 상세 워크플로 설계 |
| **[T-03](big-nodes/T-03.md)** | **Comparative Analysis** | 문서·기간·지표 비교 분석 | MVP 상세 워크플로 설계 |
| **[T-04](big-nodes/T-04.md)** | **Claim Verification** | 개별 주장 검증 | 계획 유지, 상세 설계 보류 |
| **[T-05](big-nodes/T-05.md)** | **Document Summary** | 문서 전체 요약 | 계획 유지, 상세 설계 보류 |

**범위 구분:** T-01은 특정 질문에 답하고, T-02는 사용자 지정 기준을 판정하고, T-03은 비교 가능한 값을 대조한다. T-04와 T-05는 이름만 등록한 후속 후보로, 지금 구현이 확정됐다는 의미가 아니다.

## 3. Small Node 목록 (현재 11종)

| ID | 표준 식별자 | 한국어 역할 | 구분 |
|---|---|---|---|
| **[N-01](small-nodes/N-01.md)** | `retrieve_evidence` | 근거 후보 검색 | 공통 |
| **[N-02](small-nodes/N-02.md)** | `get_source_context` | 원문·표·각주·위치 문맥 조회 | 공통 |
| **[N-03](small-nodes/N-03.md)** | `verify_grounding` | 주장과 근거·인용 정합성 검사 | 공통 |
| **[N-04](small-nodes/N-04.md)** | `draft_answer` | 질문의 답변 초안 작성 | T-01 전용 |
| **[N-05](small-nodes/N-05.md)** | `structure_criteria` | 검토 기준 항목 구조화 | T-02 전용 |
| **[N-06](small-nodes/N-06.md)** | `evaluate_criterion` | 기준별 충족 여부 판정 | T-02 전용 |
| **[N-07](small-nodes/N-07.md)** | `define_comparison` | 비교 대상·축 정의 | T-03 전용 |
| **[N-08](small-nodes/N-08.md)** | `check_comparability` | 비교 가능성 검사 | T-03 전용 |
| **[N-09](small-nodes/N-09.md)** | `build_comparison` | 정량·정성 비교 결과 구성 | T-03 전용 |
| **[N-10](small-nodes/N-10.md)** | `route_query` | 요청 확인·검증, Big Node 선택 또는 실행 전 종료 | 최상위 공통·입구 |
| **[N-11](small-nodes/N-11.md)** | `deliver_result` | 워크플로 결과·출처·검증 상태를 사용자에게 전달 | 최상위 공통·출구 |

**정의·입출력의 기준 문서:** [Small Node 설계 LIST](query-agent-node-list.md). N-01~N-09는 Big Node 내부의 Small Node, N-10·N-11은 Big Node 바깥의 최상위 공통 Small Node다.

## 4. Big Node와 Small Node의 연결

**업무별 사용 관계와 조건부 엣지는 [Big Node 상세 문서 목록](big-nodes/README.md)에서 관리한다.** 이 명칭 문서에서 실행 매핑 표는 중복하지 않는다. Small Node 단일 책임·입출력은 [Small Node 문서 목록](small-nodes/README.md)을 확인한다.

## 5. 상위 실행 단계 및 공통 용어

| 표준 명칭 | 뜻 | Big/Small Node와의 관계 |
|---|---|---|
| **ReportAnchor** | 프로젝트명 | 다른 프로젝트명·이전 이름 사용하지 않음 |
| **Harness** | 실행 관리 계층 | Big/Small Node 실행 상태·예산·오류·아티팩트 기록(MVP)을 관리. 체크포인트·재개는 미정 |
| **Query Agent** | 사용자 요청의 전체 처리 구조 | 아래 3단계의 상위 흐름 |
| **Main Agent (메인 에이전트)** | 요청을 확인하고 tool을 호출해 결과를 전달하는 LLM 주체 | N-10·N-11의 역할을 수행. Big Node를 tool로 호출하며 내부 Small Node는 직접 호출하지 않음 |
| **Query Intake & Routing** | 원본 질문·대상 범위·권한 확인, Big Node 선택 | 최상위 Small Node **N-10** |
| **Fixed Workflow** | 사전에 정의된 순서·분기를 따르는 실행 흐름 | 각 Big Node의 내부 실행 방식 |
| **Result Delivery** | 결과·출처·검증 상태·미확인 내용 제공 | 최상위 Small Node **N-11** |
| **Hybrid Retrieval** | Dense·BM25 후보 검색 및 통합 | N-01의 내부 검색 기능 |
| **RRF** | 순위 기반 후보 통합 | N-01의 내부 기능 |
| **Reranker** | 검색 후보 순위 재평가 | N-01 내부의 선택형 기능 |
| **Query Expansion** | 질의 증폭·재작성 | MVP 기본 흐름에서 제외, H-12 백로그 유지. 도입 시 Big Node 밖의 별도 노드 |

**용어 주의:** `Reranker`는 원문 근거가 사실임을 검증하는 Small Node가 아니다. 관련성 재평가와 `verify_grounding`(근거 지지 검사)은 구별한다.

## 6. 현재 MVP에서 사용하는 산출물 이름

아래는 **현재 새 MVP 설계 문서에 등장하는 이름만 모은 목록**이다. 타입 정의나 필수 필드의 최종 확정은 별도 계약 설계에서 진행한다.

| 표준 개념명 | 역할 |
|---|---|
| `ReviewRequest` | 원본 사용자 요청과 명시적 제약 |
| `RoutedTask` | N-10이 선택한 Big Node 및 해당 입력·허용 범위 (실행 불가 시 상태·사유를 N-11로 전달) |
| `RetrievalCandidates` | N-01의 검색 후보·순위·원문 참조 |
| `EvidenceBundle` | 근거 후보를 원문 문맥·위치·검증 상태와 연결한 묶음 |
| `ReviewDraft` | T-01의 주장 초안과 답하지 못한 항목 |
| `CriteriaSet` / `CriterionFinding` | T-02의 기준 목록 / 항목별 판정 |
| `ComparisonSpec` / `ComparisonFinding` | T-03의 비교 정의 / 결과 |
| `VerificationReport` | N-03의 주장·근거·인용 검증 결과 |
| `WorkflowResult` | Big Node가 메인 에이전트에게 돌려주는 tool 결과. 최종 실행 결과와 상태·근거 참조를 담으며, N-11이 사용자에게 표현하되 새로운 판정은 하지 않음 |

**경계 메모:** 기존 문서에는 `EvidenceBundle`을 검색 단계에서 제공한다고 표현한 곳과, N-02 원문 조회·보강 후 제공한다고 표현한 곳이 있다. 여기서는 **원문 근거를 연결한 묶음**이라는 의미만 통일하고, 정확한 생성·보강 경계는 데이터 계약 설계에서 결정한다.

## 7. 이름 표기 규칙

| 대상 | 규칙 | 예 |
|---|---|---|
| Big Node ID | 기존 `T-xx` 유지 (문서 연결 및 식별자 안정성) | `T-01` |
| Small Node ID | 기존 `N-xx` 유지 | `N-02` |
| 하네스 아이디어 ID | 별도 `H-xx` 사용. Big/Small Node ID와 혼용하지 않음 | `H-12` |
| Small Node 식별자 | 영어 `snake_case` | `get_source_context` |
| 산출물 개념명 | 영어 `PascalCase` | `EvidenceBundle` |
| 필드명(후보) | 영어 `snake_case` | `document_id`, `source_ref` |
| 설계 문서 | 영어 소문자 `kebab-case` 파일명 | `query-agent-mvp.md` |

**신규 문서 작성 기준:** 기능의 사용자 목적을 설명할 때 **Big Node**, 그 내부 또는 최상위 입구·출구의 최소 실행 단위를 설명할 때 **Small Node**를 우선 사용한다. 메인 에이전트와의 호출 관계를 설명할 때와 프레임워크 고유의 Tool/Node 명칭을 인용하는 경우에만 tool 등 원래 용어를 병기한다. **별도 Tiny Node 계층은 만들지 않는다.**

## 8. 관련 문서

- [Big Node별 워크플로 문서](big-nodes/README.md) · [Query Agent 전체 흐름](query-agent-mvp.md)
- [Small Node 문서 목록](small-nodes/README.md) · [Small Node LIST](query-agent-node-list.md)
- [문서 처리·하이브리드 검색 파이프라인](document-pipeline.md)
- [하네스 아이디어 백로그·결정 사항](idea-backlog.md)
