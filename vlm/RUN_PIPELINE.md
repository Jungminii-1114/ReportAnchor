> **이전 v4 실행 안내입니다. 최신 통합 코드는 [reportanchor_pipeline/README.md](reportanchor_pipeline/README.md)를 확인하세요.**

# PDF 전체 실행

실행 진입점은 `run_pipeline.py`입니다. 문서명·페이지 수를 고정하지 않습니다.

PDF → PyMuPDF 기본 파싱 → Docling 표준 파이프라인(OCR·레이아웃·표·그림 분류) → 기존 ③ 규칙 + v4 보완 → 선정 영역 이미지 → 로컬 Ollama → 출처 포함 JSON.

## 설치 및 실행

저장소 루트에서:

```bat
pip install -r vlm\pipeline\requirements.txt
python vlm\run_pipeline.py --pdf "C:\자료\보고서.pdf"
```

위 명령은 파싱·선정·이미지 생성까지만 실행합니다. VLM까지 실행:

```bat
python vlm\run_pipeline.py --pdf "C:\자료\보고서.pdf" --run
```

기본적으로 빨간 `vlm_candidate`만 호출합니다. 주황 `review_required`도 실험적으로 실행하려면 `--include-review`를 추가합니다. 파랑·초록은 호출하지 않습니다. 주황은 자동 승인된 보완 대상이 아닙니다.

Docling을 별도 가상환경에 설치했다면:

```bat
python vlm\run_pipeline.py --pdf "C:\자료\보고서.pdf" --docling-python "C:\Users\twobi\anaconda3\envs\docling_eval\python.exe" --run
```

현재 Python에는 PyMuPDF·numpy·scipy·Pillow가 필요합니다. 별도 Docling 환경에는 `pipeline/requirements.txt`의 Docling·EasyOCR 의존성이 필요합니다. 최초 Docling/EasyOCR 모델 다운로드는 인터넷 연결이 필요하며, 이전의 Hugging Face 다운로드 오류가 해결되어야 새 변환을 실행할 수 있습니다. 로컬 Ollama가 실행 중이고 지정 모델이 설치되어 있어야 `--run`이 동작합니다.

## 출력

`vlm/outputs/doc_<PDF해시>/` 아래에 저장합니다.

- `results/.../parser_python.json`: 기본 파서 결과
- `results/.../docling/`: 새로 생성한 페이지별 Docling 결과
- `selection_v4/`: 이번 PDF에 새로 적용한 v4 선정 결과
- `vlm/inputs/`: 선정 좌표를 이용해 자동 생성한 PNG·기본 텍스트
- `vlm/tasks.json`: 자동 생성한 VLM 작업 목록
- `vlm/results/<모델>/`: 원본 응답·추출 결과·실행 요약
- `document_result.json`: 문서 정보와 페이지별 기본 파싱·Docling·선정·VLM 결과를 모은 JSON

문서 ID·PDF 해시·페이지·좌표·선정 사유를 보존합니다. 기본 추출과 VLM 출력을 별도로 보관하며, RAG 인덱싱이나 정확도 검증을 자동 완료한 것으로 취급하지 않습니다. 회전된 페이지는 좌표 오류 방지를 위해 현재 명시적으로 거부합니다.

동일 PDF·선정 이미지·프롬프트·모델·옵션의 성공한 VLM 출력은 재사용합니다. 실패한 호출은 재시도합니다. 원문 내용이 바뀌면 PDF 해시별로 분리됩니다.

## 확인 범위

- 기존 AX 전체 14페이지를 기본 파싱하고, 해시가 일치하는 기존 Docling 결과를 재사용하여 v4 → 6개 검토 영역 PNG → 실행 목록 → 문서 JSON까지 실행 확인(실제 모델 호출 없음).
- 새로 생성한 임의 이름의 PDF로 통합 테스트 통과. Docling 데이터와 Ollama HTTP 응답은 명시적인 모의 데이터 사용. 실제 파싱·규칙·이미지 생성·요청·출력 저장·재실행 응답 재사용·해시 불일치 거부 검사.
- 실제 Docling 신규 추론과 실제 Ollama 추론을 포함한 종단 실행은 이 환경에서 검증하지 못했습니다. 기존 6개 VLM 실험 결과는 새 자동 파이프라인의 실행 결과가 아닙니다.
- v4 규칙은 기존 실험에서 고정한 규칙이며 새 PDF 전반의 선정 정확도를 보장하지 않습니다. 도식 프롬프트는 이번 연결에서 추가했고 추출 품질은 미평가입니다.
