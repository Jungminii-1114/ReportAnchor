# RAG 데이터 준비: 로더와 청킹

## 현재 완료 범위

추출된 `manifest.json`, `pages.jsonl`, `blocks.jsonl`을 읽고 문서·버전·페이지 참조를 검증한 뒤, 검색용 `chunks.jsonl`을 생성합니다.
PDF 재파싱, VLM 호출, 임베딩, 검색 인덱스, 답변 생성은 이 단계에 포함하지 않습니다.
이 문서는 현재 PyMuPDF 데모의 입력을 기준으로 한 구현 계약입니다. 최종 Parser/VLM 출력과의 계약은 해당 담당자와 별도 합의해야 합니다.

## 주요 데이터 4개

| 파일 | 저장 단위 | 목적 | 사용하는 시점 |
|---|---|---|---|
| `manifest.json` | 문서 1개당 JSON 객체 1개 | 문서 ID, 원본 SHA-256, 출처 URL, 페이지 수, 파서 버전 관리 | 입력 등록·버전 확인·출처 표시 |
| `pages.jsonl` | PDF 페이지 1개당 한 줄 | 페이지 전체 텍스트와 페이지 상태 보관. 빈 페이지도 기록 | 페이지별 확인·검색 결과 주변 문맥 확장 |
| `blocks.jsonl` | 추출 텍스트 블록 1개당 한 줄 | 블록 텍스트와 원문 페이지·좌표를 보관 | 청킹의 직접 입력·원문 위치 추적 |
| `chunks.jsonl` | 검색 청크 1개당 한 줄 | 인접 블록을 검색에 사용할 크기로 묶고 원문 참조 보존 | 다음 단계의 BM25·임베딩 검색 입력 |

`manifest.json`은 문서 메타데이터입니다. 청킹 설정은 별도의 `chunking_config`이며, 이번 처리의 진단 결과는 보조 파일 `chunking_report.json`에 저장합니다.

```text
manifest.json ─ 문서 식별·버전·출처 확인 ─┐
pages.jsonl ─── 페이지 목록·빈 페이지 확인 ├→ 로더 검증
blocks.jsonl ── 텍스트·블록 ID·좌표 ─────┘
                                          ↓
                                 인접 블록 청킹
                                          ↓
                                   chunks.jsonl
                                          ↓
                           BM25 / 임베딩 검색 [다음 단계]
```

네 파일을 합쳐 매번 LLM에 전송하는 구조가 아닙니다. 검색기는 주로 `chunks.jsonl`을 읽고, 원문 확인·문맥 확장 때 나머지 데이터를 참조합니다.

## 입력 로더: `load_document(input_dir)`

`ParsedDocument` 객체에 문서 정보, 페이지 목록, 블록 목록, 입력 파일 해시를 담습니다.

- 세 파일의 문서 ID와 원본 버전이 일치하는지 검사합니다.
- 전체 페이지 기록, 페이지 번호 범위, 중복 페이지·블록 ID, 문자 수, 출처 페이지·좌표 형식을 검사합니다.
- 텍스트가 있는 페이지에 블록이 전혀 없는 등 참조가 깨진 입력을 거부합니다.
- 같은 페이지에서는 `blocks.jsonl`의 순서를 유지합니다. `block_index`는 추출 시의 식별 정보이므로 읽기 순서라고 가정해 다시 정렬하지 않습니다.
- PDF 파일 자체를 다시 열지 않습니다. 입력 JSON의 일관성을 확인하는 것이며, 원문 인식 정확성을 보증하는 검사는 아닙니다.

```python
from rag_data import ChunkingConfig, load_document, build_chunks

document = load_document("data/processed/pymupdf_demo/kistep_2025_plan_0530d100e085")
chunks = build_chunks(document, ChunkingConfig(max_chars=1000, overlap_chars=120))
```

## 청킹 규칙

- 한 페이지 안에서 인접 블록을 묶습니다. 페이지 경계는 넘지 않습니다.
- 기본 최대 길이는 **1,000 Unicode 문자**입니다. 토큰 수가 아닙니다.
- 최대 **120문자 범위의 완전한 후미 블록**을 다음 청크에 반복합니다. 긴 블록은 반복하지 않아 실제 중첩이 0일 수도 있습니다.
- 일부 명확한 제목 표현을 정규식으로 만나면 경계를 나눕니다. 완전한 절·사업 구분 탐지는 아닙니다.
- 블록 자체가 최대 길이를 넘으면 줄바꿈·문장·공백 경계 순으로 분할합니다. 경계가 없으면 문자 기준으로 자르고 표시합니다.
- 원문 `text`는 그대로 두고, 검색용 `search_text`에만 Unicode·공백 정규화를 적용합니다.
- 합계·단위·연도를 추론하거나 표 내용을 재작성하지 않습니다. 짧은 제목·목차도 임의로 삭제하지 않습니다.
- 입력과 설정이 같으면 같은 청크 ID가 나옵니다. 입력 블록/페이지·청킹 설정·알고리즘 버전이 바뀌면 ID가 바뀝니다.

## 청크의 핵심 필드

| 필드 | 의미 |
|---|---|
| `chunk_id` | 청크 고유 ID. 문서·버전·입력 지문·청킹 설정·내용을 반영 |
| `doc_id`, `version_sha256` | 어느 문서와 원본 버전에서 나왔는지 |
| `text` | 인용·확인에 사용하는 원문 블록 문자열. 블록 사이에는 줄바꿈 추가 |
| `search_text` | 검색 입력용 정규화 문자열 |
| `pdf_pages` | 연결된 실제 PDF 페이지 번호. 현재 구현에서는 한 페이지 |
| `source_block_ids` | 청크를 구성한 블록 ID 목록 |
| `source_spans` | 각 블록의 문자 구간과 원문 좌표. `start_char`는 0부터, `end_char`는 미포함 |
| `chunking_config`, `chunker_version` | 청크 생성 조건과 알고리즘 버전 |
| `verification_status` | 현재 `unverified`. 청크 생성 성공을 사실 검증으로 취급하지 않음 |

`source_spans`의 원문 구간들을 줄바꿈으로 연결하면 청크의 `text`가 정확하게 복원되어야 합니다.
`source_block_ids`만 남기면 긴 블록을 나누었을 때 어느 부분을 인용했는지 불명확하므로 문자 구간도 보존합니다.

## 실행

```bash
cd "/Users/ijeongmin/ai_study/Personal_project/Report Agent"

./.venv/bin/python 02_build_chunks.py \
  --input-dir data/processed/pymupdf_demo/kistep_2025_plan_0530d100e085
```

기본 결과:

```text
data/processed/rag/kistep_2025_plan_0530d100e085/3382f591c928/
├── chunks.jsonl
└── chunking_report.json
```

입력 세 파일은 기존 추출 폴더에 남습니다. 출력은 설정 ID별 별도 폴더에 저장하며, 같은 입력/설정으로 재실행하면 같은 결과 위치를 갱신합니다.
`--max-chars`, `--overlap-chars`, `--out`으로 조건·결과 폴더를 바꿀 수 있습니다. CLI의 상대 경로는 프로젝트 루트 기준입니다.

## 이번 실행 결과

| 항목 | 결과 |
|---|---:|
| 입력 페이지 | 117 |
| 입력 블록 | 3,105 |
| 생성 청크 | 180 |
| 최대 청크 길이 | 1,000문자 |
| 블록이 없는 페이지 | 2, 4쪽 |
| 최대 길이 초과로 나눈 블록 | 0 |
| 원문 블록 문자 커버리지 | 99,894문자, 모두 보존 |

99,894는 `blocks.jsonl`의 원문 문자열 합계입니다. `pages.jsonl` 텍스트는 레이아웃 공백·정렬 방식이 달라 문자 수가 다를 수 있습니다.
블록 누락 없음, 원문 구간→청크 복원, 길이 제한, 청크 ID 고유성을 확인했습니다. 이 수치는 검색 품질 평가가 아닙니다.

## 테스트

```bash
./.venv/bin/python -m unittest discover -s tests -v
```

새 테스트 10개는 페이지/제목 경계, 원문 순서, 중첩, 긴 Unicode 블록, ID 재현성, 잘못된 참조 거부, 원문 변조 검출, 입력 보존, CLI를 검사합니다.
기존 PDF 추출 테스트 9개와 합쳐 19개입니다.

## 팀 연결 및 다음 작업

- Parser/VLM 담당자: 문서·블록 ID, 페이지·좌표, 구조화 표/제목 정보의 의미를 함께 합의합니다.
- RAG 담당자: `chunks.jsonl`을 사용해 BM25 기준선을 만들고 질문별 정답 근거를 정의합니다.
- 하네스 담당자: 이후 검색 함수의 호출·결과 참조·오류·버전 인터페이스를 연결합니다.

원문과 검색 데이터는 현재 `.gitignore`에 의해 제외됩니다. 재현 코드·스키마 설명을 Git에 관리하고, 팀원은 동일 입력으로 청크를 재생성할 수 있습니다.
