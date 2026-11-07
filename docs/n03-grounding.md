# N-03 주장·원문 정합성 검증

N-04의 `ReviewDraft`와 N-02의 `EvidenceBundle`을 받아 주장별 판정 결과를 반환하는 독립 실행 노드이다. 공통 OllamaClient를 사용하며 N-04의 대화 기록을 전달하지 않는다.

## 처리 순서

입력 검사 → 근거 조회표 → 참조가 있는 주장 선택 → 주장별 원문 조회 → 숫자 참고 정보 → 독립 메시지 구성 → 모델 호출 → 응답 검사 → 결과 취합 순서이다.

- 근거 참조가 없는 주장은 판정하지 않고 `diagnostics.select_claims.excluded_refs`에 기록한다.
- 없는 근거 참조는 해당 주장의 실행 실패이다. 다른 주장 검증은 계속한다.
- 숫자 참고 정보는 숫자 문자열의 등장 여부이다. 단위·연도 의미를 검증하는 규칙 엔진이 아니며, 계산 결과의 미등장을 모순으로 확정하지 않는다.
- 숫자 참고 단계만 실패하면 빈 참고 정보와 원문으로 모델 검증을 계속한다.
- 모델 응답은 `claim_id`, `verdict`, `reason`만 가진 JSON 객체여야 한다. 잘못된 JSON·중복 키·추가 키·다른 주장 ID·잘못된 판정값·빈 이유는 실행 실패이다.
- `evidence_refs`는 모델 응답이 아닌 실제 전달한 근거에서 코드로 채운다.
- `supported`, `contradicted`, `insufficient`는 업무 판정이다. 어느 값이든 유효한 응답이면 처리 완료이다.

## 결과와 실패 처리

| 상태 | 조건 | data |
| --- | --- | --- |
| success | 모든 대상 처리 완료 또는 검증 대상 없음 | VerificationReport, 대상이 없으면 빈 checks |
| partial | 일부 대상 처리 실패 | 완료된 checks를 보존한 VerificationReport |
| error | 전체 입력·설정 오류 또는 모든 대상 처리 실패 | null |

실패한 주장 ID·오류 코드·재시도 가능 여부는 `diagnostics.verify_claims.failed_items`에 기록한다. 단계별 진단도 별도로 남긴다. `outcome`은 항상 null이다. 재시도 가능 여부를 표시하되 N-03 자체는 자동 재시도하지 않는다. HTTP 408·429·5xx와 연결·시간 초과는 재시도 가능한 실패이다.

## Python 호출

```python
from rag.llm.ollama_client import OllamaClient
from rag.nodes.verify_grounding import VerifyGroundingNode

client = OllamaClient(model="설치한-모델명", base_url="http://localhost:11434")
result = VerifyGroundingNode(client).run(request_ref, draft, evidence_bundle)
```

## CLI

프로젝트 루트에서 실행한다. 아래 입력 파일은 각각 **현재 계약으로 생성한 N-04·N-02 결과**이다. 파일명은 사용자가 저장한 이름으로 바꾸어야 한다. 두 파일의 request_ref가 같아야 한다. 기존 evidence_id/items 형식은 지원하지 않는다.

```bash
python -m rag.nodes.verify_grounding --draft outputs/n04_js_demo.json --evidence-bundle outputs/n02_js_demo.json --model "설치한-모델명" > outputs/n03_js_demo.json
```

- 기본 서버는 `http://localhost:11434`이며 `--base-url`로 변경할 수 있다.
- `--timeout`의 기본값은 주장별 120초이다. 주장은 순차 호출한다.
- `--provider cloud`는 Ollama Cloud의 `https://ollama.com`과 환경 변수 `OLLAMA_API_KEY`를 사용한다. 이 모드에서는 선택된 주장과 원문 근거가 외부로 전송된다.
- 표준 출력은 결과 JSON 한 개이다. 종료 코드는 success/partial이면 0, error이면 2이다. partial은 전체 성공을 뜻하지 않으므로 JSON status와 failed_items도 확인해야 한다. 인자 문법 오류는 argparse의 사용법 메시지와 종료 코드 2로 처리한다.
- 파일을 읽거나 모듈을 import하는 것만으로 모델을 호출하지 않는다.

## 검증 범위와 남은 작업

자동 테스트는 가짜 모델 응답으로 상태 분기·출처 연결·JSON 검사·입력 보존·CLI 계약을 검증한다. 실제 LLM의 판정 정확도, 환각 억제, 프롬프트 공격 방어 성능을 입증하는 실험은 아니다. supported도 전달된 추출 원문 기준 판정이며 PDF/OCR 정확도 보증이 아니다.

- [x] N-03 단독 실행과 결과 취합
- [x] 기존 N-02·N-04 JSON 로드
- [ ] 실제 모델·사람 정답을 이용한 판정 성능 평가
- [x] T-01에서 N-01 → N-02 → N-04 → N-03 연결 — [실행 가이드](t01-document-qa.md)
- [x] contradicted 주장만 1회 보정하고 재검증하는 T-01 순환
- [x] 웹 데모에서 T-01을 통한 N-03 실행·판정 표시 — [웹 가이드](web-workspace.md)
