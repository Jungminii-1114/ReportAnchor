# Big Node — 업무별 워크플로 문서 목록

> **정본:** 목적별 **Small Node 사용 관계·실행 순서·조건부 엣지·보류/결과 정책**을 관리한다. Small Node 함수와 세부 입출력 계약은 [Small Node 문서](../small-nodes/README.md)를 참고한다.
>
> **최상위 구조:** [N-10](../small-nodes/N-10.md) → 선택된 Big Node → [N-11](../small-nodes/N-11.md). 외부 입구와 출구는 Big Node 내부 구성에 포함하지 않는다.
>
> **호출 방식:** 각 Big Node는 메인 에이전트(N-10·N-11)가 호출하는 tool이다. 내부 실행은 고정 워크플로로 끝내고 `WorkflowResult`를 메인 에이전트에게 돌려준다. [상세](../query-agent-mvp.md#1-전체-실행-흐름)

| Big Node | 목적 | 상태 | 연결된 Small Node 문서 |
|---|---|---|---|
| [T-01 · Document QA](T-01.md) | 문서 근거 질의응답 | MVP · 개념 계약 확정 / 구현 미착수 | [N-01](../small-nodes/N-01.md) · [N-02](../small-nodes/N-02.md) · [N-04](../small-nodes/N-04.md) · [N-03](../small-nodes/N-03.md) |
| [T-02 · Criteria Review](T-02.md) | 명시적 기준 충족 검토 | MVP · 1차 설계 | [N-05](../small-nodes/N-05.md) · [N-01](../small-nodes/N-01.md) · [N-02](../small-nodes/N-02.md) · [N-06](../small-nodes/N-06.md) · [N-03](../small-nodes/N-03.md) |
| [T-03 · Comparative Analysis](T-03.md) | 문서·기간·지표 비교 | MVP · 1차 설계 | [N-07](../small-nodes/N-07.md) · [N-01](../small-nodes/N-01.md) · [N-02](../small-nodes/N-02.md) · [N-08](../small-nodes/N-08.md) · [N-09](../small-nodes/N-09.md) · [N-03](../small-nodes/N-03.md) |
| [T-04 · Claim Verification](T-04.md) | 개별 주장 검증 | 후속 계획 · 미정 | 미정 |
| [T-05 · Document Summary](T-05.md) | 문서 전체 요약 | 후속 계획 · 미정 | 미정 |

**문서 책임:** Small Node 문서에는 업무별 사용 예시나 Big Node의 엣지를 작성하지 않고, Big Node 문서에서만 연결 관계를 관리한다. 별도의 Tiny Node 계층은 두지 않는다.

[Query Agent MVP](../query-agent-mvp.md) · [명칭 기준](../naming-conventions.md) · [Small Node 목록](../small-nodes/README.md)
