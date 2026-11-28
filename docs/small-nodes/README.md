# Small Node — 개별 계약 문서 목록

> **정본:** 각 Small Node의 단일 책임·내부 함수·입출력·실패 상태를 관리한다. Big Node별 사용 예시나 노드 간 조건부 엣지는 포함하지 않는다.
>
> **현재:** N-01~N-06과 최상위 입출구 N-10·N-11의 **MVP 개념 계약 설계를 완료**했으며, 실제 Python 타입·프롬프트·구현/테스트는 미완료다. 비교 분석 전용 노드 **N-07·N-08·N-09도 MVP 개념 계약을 확정(2026-11-28)**해 N-01~N-11 전체의 MVP 개념 계약이 완료됐다. (각 노드의 확정 요약은 아래 표 및 개별 문서 참고)
>
> **공통 반환 계약(확정 2026-11-01):** **[NodeResult[T] — 공통 블록·상태·단계별 진단·시각화](common-result-contract.md)**. N-01~N-11의 모든 반환은 `status`, `data`, `diagnostics`, `outcome` 4필드를 사용한다.

| Small Node | 역할 | 설계 상태 |
|---|---|---|
| [N-01 · `retrieve_evidence`](N-01.md) | 검색 후보 탐색 | 입출력 개념 계약·상태 처리 확정 / 상세 타입·검색 설정 미정 |
| [N-02 · `get_source_context`](N-02.md) | 원문 위치 매핑·고정 길이 문맥 확장·출처 보존 | 입출력·예외·오프셋 규칙 확정 / 실제 타입·구현 미정 |
| [N-03 · `verify_grounding`](N-03.md) | 주장·판정·비교 결과와 원문 근거의 정합성 검사 | **MVP 개념 설계 완료 (입출력·흐름·상태·구조도)** / 구현·테스트 미완료 |
| [N-04 · `draft_answer`](N-04.md) | 원문 근거를 이용한 질문 답변 초안 작성 | 문장 단위 주장·`answer` 제거·부분 답변·전체 무참조 답변 차단·보정 호출 확정 / 구현 미완료 |
| [N-05 · `structure_criteria`](N-05.md) | 명시된 검토 기준을 판정 항목으로 구조화 | **B 방식·평면형 AND/OR·기본 예외 정책 확정** / 상세 코드·스키마·구현 미정 |
| [N-06 · `evaluate_criterion`](N-06.md) | 조건별 충족·미충족·확인 불가 판정 | **MVP 개념 계약 확정 (조건별 판정·정량 Python 하이브리드·무참조 오류 차단·보정 미도입)** / 타입·구현 미완료 |
| [N-07 · `define_comparison`](N-07.md) | 단일 문서·정량 지표 1개·서로 다른 기간 2개의 비교 축 정의 | **MVP 개념 계약 확정 (LLM 1회 구조화+Python 검증·바인딩·`ComparisonDefinition` 공개 구조·모호 요청 기본 동작·`unsupported_comparison` 우선)** / 타입·구현 미완료 |
| [N-08 · `check_comparability`](N-08.md) | 두 대상의 값·연도·부호·단위·수치 성격·집계 범위·각주 대조와 비교 가능성 판정 | **MVP 개념 계약 확정 (LLM 1회 추출+Python 토큰·부호·연도 검증·4대 차원·Strong Kleene 3분류·부분 값 보존·`conversion_plan`)** / 타입·구현 미완료 |
| [N-09 · `build_comparison`](N-09.md) | 정량 비교 결과 구성(환산·차이·증감률)과 N-03 검증용 값 주장 생성 | **MVP 개념 계약 확정 (순수 Python 0-LLM·`Decimal` 정확값/표시값 분리·기준값 ≤ 0 증감률 미산출·`values_only_hold`·대상별 값 주장 최대 2건)** / 타입·구현 미완료 |
| [N-10 · `route_query`](N-10.md) | 요청·권한·범위 확인과 작업 선택 | **MVP 개념 계약 확정 (사전 코드 검증·AI 1회 라우팅·하네스 인자 조립·fail-closed 오류 분리)** / 타입·구현 미완료 |
| [N-11 · `deliver_result`](N-11.md) | 검증된 결과·출처·상태 전달 | **MVP 개념 계약 확정 (코드 구조화 조립+AI 1회 요약·정형 폴백·`result.kind` 통일·오류/탈락 원문 비노출)** / 타입·구현 미완료 |

[Small Node 설계 LIST](../query-agent-node-list.md) · [Big Node별 워크플로 목록](../big-nodes/README.md) · [명칭 기준](../naming-conventions.md)
