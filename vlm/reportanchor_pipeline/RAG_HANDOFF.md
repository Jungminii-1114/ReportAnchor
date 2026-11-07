# RAG 입력 후보 계약 초안

`rag_candidates.jsonl`의 각 줄은 독립 JSON 객체입니다. 문서 검색·청킹에 전달할 후보 형식이며 최종 승인 데이터가 아닙니다.

| 필드 | 의미 |
|---|---|
| `doc_id`, `version_sha256` | 원본 PDF 내용 해시 기반 문서·버전 식별 |
| `block_id` | 문서·페이지·블록 식별 |
| `pdf_page` | PDF 뷰어 기준 1부터 시작하는 페이지 번호 |
| `bbox`, `coordinate_system` | 원본 페이지 좌상단 기준 PDF 포인트 좌표. 현재 회전 없는 PDF에서 확인 |
| `kind`, `extraction_method` | 텍스트·표·그림 등의 유형과 추출 경로 |
| `text` | 기본 텍스트 후보. 표/보완 레코드에서는 빈 문자열일 수 있음 |
| `structured_data` | Docling 표의 셀·그리드 구조. 해당 레코드에만 존재 |
| `native_alternatives` | 표 내부의 기본 파서 텍스트 대안 |
| `input_bbox`, `source_refs`, `rule_version` | 선택·보완 레코드의 문맥 범위와 원천 참조·규칙 |
| `ocr`, `vlm` | 보완 실행 상태·결과. 해당 레코드에만 존재 |
| `verification_status` | 현재 `unverified` |
| `rag_eligible` | 현재 모두 `false`: 아직 승인하지 않은 후보 |

샘플 3개는 모두 가상 데이터이며 실제 PDF의 글자·수치·좌표·해시를 포함하지 않습니다. 기본 텍스트, 정상 형식 VLM 결과, VLM 실패 상태의 형식만 보여줍니다. 모델 출력은 기본 원문을 덮어쓰지 않습니다. `source_refs`의 Docling 참조를 따라가려면 원본 `document_result.json`의 페이지 데이터를 사용합니다.

`vlm.extraction.items` 내부 필드는 아직 유형별 공통 스키마로 정규화되지 않았습니다. `col`/`column`, 관계 필드 등 모델 반환 표현이 다르므로 RAG 단계에서 바로 읽을 최종 표 계약으로 간주하면 안 됩니다.

다음 사항을 담당자와 합의해야 합니다.

- 텍스트·표·보완 결과를 각각 청킹할지, 한 근거 묶음으로 연결할지.
- 표 헤더·제목·단위·각주를 어떤 메타데이터로 연결할지.
- 성공 JSON, 기존 추출, 실패·미확인을 어떻게 구분하고 승인할지.
- 원문과 보완 결과가 다를 때 선택·검토 규칙.

원본 문서와 모델 결과의 정답 여부를 승인한 뒤 `rag_eligible`을 바꾸는 절차가 필요합니다. 이 파이프라인은 승인이나 RAG 인덱싱을 자동 수행하지 않습니다.
