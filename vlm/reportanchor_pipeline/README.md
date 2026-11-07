# ReportAnchor PDF 통합 실행본

[현재 구현 상태](CURRENT_STATUS.md) · [RAG 후보 계약](RAG_HANDOFF.md)

PDF 전체 → PyMuPDF 기본 추출 → Docling 레이아웃·표 구조 및 필요한 EasyOCR → Python 영역 선정 → 선택적 OCR 재검토 또는 VLM → 원본·보완 결과와 출처 저장.

실행 진입점은 이 폴더의 `run_pipeline.py`입니다. 이전 v4/v5/audit 코드를 순차 실행할 필요가 없습니다.

## 실행

저장소 루트에서 실행합니다. Python/conda 환경에 `requirements.txt`의 의존성이 필요합니다. Docling/EasyOCR은 최초 실행 시 모델을 다운로드할 수 있습니다.

```bash
python -m pip install -r vlm/reportanchor_pipeline/requirements.txt
python vlm/reportanchor_pipeline/run_pipeline.py --pdf path/to/input.pdf --output local_results/document
```

위 명령은 기본 파서·Docling·선택 규칙·결과 저장을 실행합니다. 추가 OCR 재검토와 클라우드 요청은 하지 않습니다. `local_results`는 저장소 밖의 로컬 폴더를 지정하거나 `.gitignore`에 추가하세요.

```bash
python vlm/reportanchor_pipeline/run_pipeline.py --pdf path/to/input.pdf --output local_results/document --run-ocr --max-ocr-calls 3 --run-cloud --max-vlm-calls 3
```

위 명령은 OCR/VLM 보완을 각각 실행당 최대 3회까지 수행합니다. 연결 확인용 상한이며 전체 후보가 모두 처리되는 설정은 아닙니다. 후보 수와 계정 한도를 확인한 뒤 상한을 정하세요. 실패 요청도 횟수에 포함합니다. 기본 모델은 기존 실험에서 사용한 `deepseek-v4.1-flash`이며 현재 계정의 접근 가능 여부는 실제 호출 시 확인합니다. `--model`로 바꿀 수 있습니다.

API 키는 `OLLAMA_API_KEY` 또는 숨김 입력으로 전달하며 파일에 저장하지 않습니다. 401/403/429 시 해당 실행의 남은 클라우드 요청을 중단합니다. 자동 API 재시도는 없습니다.

- `--cache-root`: 기존 vlm 폴더 또는 `parser_python.json`이 있는 폴더. PDF SHA256이 일치하는 캐시만 사용.
- `--skip-docling`: 새 Docling 실행 없이 캐시만 확인. 누락·오류 페이지를 성공 처리하지 않음.
- `--start-page`, `--end-page`: PDF 뷰어 기준 처리 범위.
- `--run-ocr`: OCR 재검토 후보만 EasyOCR로 다시 읽기. 기본 CPU, `--ocr-gpu`로 GPU 지정.
- `--run-cloud`: VLM/구조 검토 후보의 클라우드 호출 허용.
- `--retry-failed`: 동일 설정에서 실패/빈 OCR을 명시적으로 재시도.

정상 결과는 입력·프롬프트·모델이 일치할 때 재사용합니다. 같은 출력 폴더에서 PDF/선택 규칙이 바뀌면 거부합니다. API 요청 직후 강제 종료로 저장이 안 된 경우에는 중복 요청 가능성이 있습니다.

## 통합한 코드

| 코드 | 역할 |
|---|---|
| `pipeline/01_parser_python.py` | 기존 기본 파서. 원문·좌표·표·검출 오류 수집 |
| `docling_page_worker.py` | 기존 Docling/EasyOCR 페이지별 실행부. 성공 상태 판정 보정 |
| `selection_policy.py` | 마지막 개선 버전의 source-policy 함수만 분리. 실험 CLI 제거 |
| `run_pipeline.py` | 기존 integrate 흐름을 재구성. 영역 OCR·좌표 변환, OCR 보조 프롬프트, 실행 관리·결과 저장 연결 |

기존 `prepare_targeted_ocr.py`, `run_ocr_comparison.py`에서 실험한 영역 OCR·좌표 변환·대상 범위 제한·미검증 OCR 보조 방식이 통합 실행부에 반영돼 있습니다. 모델 비교용 개별 실행 파일을 먼저 돌릴 필요는 없습니다.

## 현재 Python 선택 규칙

| 규칙 | 설명 |
|---|---|
| 기존 표 재사용 | 셀 글자와 PDF 원문의 위치·내용, 셀 겹침, 본문 열의 헤더 범위를 확인하고 일치하는 표는 추가 호출에서 제외 |
| 구조 보완 | 글자가 추출돼도 조직도·그래프·흐름도의 관계 해석이 필요하면 후보로 선택. 약한 그림 분류도 글자 라벨 등 단서와 함께 고려 |
| 숫자와 문맥 | 큰 숫자 주변의 제목·단위·설명을 입력 범위에 포함하고, 숫자가 많은 큰 이미지를 사진으로 무조건 제외하지 않음 |
| 연표 | 연도·날짜·시대 표시와 사건 설명을 묶어 관계 해석의 문맥 확보 |
| 사진과 글자 분리 | 사진 본문은 보통 제외. 위쪽 제목·저자 글자가 원문으로 확보되지 않으면 띠 영역을 OCR 후보로 선정 |
| 경계 보완 | 인접 그림 조각을 묶고 가까운 제목·캡션·각주를 고려해 잘림 완화 |
| 미확인 보존 | 불확실한 영역을 성공 처리하지 않음. OCR 점수나 박스 포함만으로 정확도를 인증하지 않음 |

문서별 정답 수치·이름을 하드코딩하지 않습니다. 다만 개발 문서에 맞춰 수정한 규칙이므로 일반화는 별도 평가가 필요합니다. 두 추출기 모두 못 읽은 내용이나 원문과 관계가 맞지 않는 내용을 완전히 검출하지는 못합니다.

## 실행 분기

- `parser_only`: 기존 표 재사용 후보, 추가 호출 없음.
- `ocr_candidate`: 추가 OCR 플래그가 있을 때 재검토. 자동 VLM 승격 없음.
- `vlm_candidate`, `review_required`: 클라우드 플래그가 있을 때 상한 내 VLM 보완.
- `exclude_vlm`: 원본 보존, VLM 호출 없음.
- `review_unresolved`: 자동 호출하지 않고 검토 대기.

## 출력

- `document_result.json`: 전체 기본 추출·Docling·선택·보완 결과. 원문 유지.
- `review.html`과 `crops/`: 선택 영역 및 OCR/VLM 결과 검토 화면.
- `rag_candidates.jsonl`: 문서·블록 ID, 페이지·좌표, 추출 경로·구조·검증 상태를 묶은 후보.
- `responses/`: 파싱 실패 시에도 저장하는 원문 응답.
- `cache/`: 같은 PDF의 재사용 캐시.

일치하는 글자·위치의 중복만 정리합니다. 표 내부의 기본 텍스트는 표 레코드의 `native_alternatives`로 연결합니다. 서로 다른 OCR/VLM 해석을 자동으로 정답 하나로 합치지 않습니다. 현재 `rag_eligible=false`이며 RAG 최종 승인·청킹·인덱싱은 별도입니다.

## 검사

```bash
python -m unittest discover -s vlm/reportanchor_pipeline/tests -v
```

JSON 형식·오류 응답 보존 검사는 별도 자료 없이 실행됩니다. 호출 상한·재개·OCR 분기·캐시 누락 검사는 `REPORTANCHOR_TEST_INPUTS`로 테스트 자료 폴더를 지정한 경우 실행하며, 실제 OCR/API 경계는 테스트 대역을 사용합니다. 해당 자료 폴더에는 테스트 코드에 명시된 PDF와 캐시 구조가 필요합니다. 테스트 자료와 실제 검증 산출물은 이 변경에 포함하지 않습니다.

실제 모델 응답 정확도와 회전/특이 CropBox PDF의 좌표 처리는 추가 검증이 필요합니다.
