## 목적: blocks.jsonl과 chunks.jsonl의 구조 및 활용 방법 공유

현재 PyMuPDF 추출 데모와 RAG 데이터 준비 코드에서 **실제로 생성한 파일**을 기준으로 정리합니다. 여기서 feature는 JSON의 각 필드와 메타데이터를 뜻합니다.

| 구분 | `blocks.jsonl` | `chunks.jsonl` |
|---|---|---|
| 역할 | 추출 결과와 원문 위치를 보관 | 검색에 사용할 텍스트 단위를 보관 |
| 한 줄의 의미 | 추출된 텍스트 블록 1개 | 검색 청크 1개 |
| 생성 단계 | PyMuPDF 텍스트 추출 | RAG 데이터 준비·청킹 |
| 주요 활용 | 원문 추적, 문맥 확장, 청킹 재생성 | BM25·임베딩 검색, Agent에 제공할 근거 선택 |
| 현재 레코드 수 | 3,105개 | 180개 |

두 파일은 JSONL 형식이므로 **각 줄을 독립적인 JSON 객체로 읽습니다.** 아래 JSON 예시는 가독성을 위해 한 레코드를 여러 줄로 펼쳤습니다.

```text
PDF → 추출 블록(blocks) → 검색 청크(chunks) → 검색 결과 → Agent의 근거 기반 답변
           ↑                    │
           └── 블록 ID·문자 구간·좌표로 원문 추적 ──┘
```

## 1. blocks.jsonl: 추출한 텍스트와 원문 위치

블록은 PyMuPDF가 추출한 텍스트 단위입니다. 한 블록이 문장·문단·표의 한 행과 정확히 일치한다고 보장하지는 않습니다. 같은 문장이 두 블록으로 나뉘거나, 여러 줄이 한 블록에 들어갈 수 있습니다.

### 최상위 필드 10개

| 필드 | 자료형 | 예시 | 의미·활용 |
|---|---|---|---|
| `doc_id` | string | `doc_0530d100e08517b6` | 어느 문서에서 추출했는지 식별. 페이지·청크와 연결하는 키 |
| `version_sha256` | string | 원본 PDF의 64자리 SHA-256 | 같은 원본 버전의 레코드인지 확인 |
| `block_id` | string | `...:p0005:b0013` | 블록 고유 ID. 청크에서 원문 블록을 찾는 키 |
| `pdf_page` | integer | `5` | PDF 파일의 물리적 페이지. 1부터 시작하며 인쇄된 쪽번호와 다를 수 있음 |
| `block_index` | integer | `13` | 파서가 부여한 페이지 내부 블록 번호. 검색 순위나 읽기 순서를 의미하지 않음 |
| `kind` | string | `text` | 블록 종류. 현재 데모는 텍스트 블록만 저장 |
| `text` | string | 추출된 원문 문자열 | 청킹의 직접 입력. 원래 공백·줄바꿈도 포함 |
| `source_locator` | object | 아래 표 참고 | 원문에서 블록의 위치를 찾기 위한 정보 |
| `extraction_method` | string | `native_text` | 현재는 PDF 텍스트 레이어에서 추출했다는 표시 |
| `verification_status` | string | `unverified` | 원문 의미·수치의 검증 상태. 추출 성공과 사실 검증을 구분 |

현재 `doc_id`는 원본 SHA-256의 앞 16자에서 생성합니다. 따라서 파일 내용이 바뀌면 `doc_id`와 `version_sha256`이 함께 바뀝니다. 여러 개정판을 묶는 영구적인 논리 문서 ID는 현재 따로 없습니다.

`block_id`의 `p0005`는 5쪽, `b0013`은 블록 번호 13을 뜻합니다. **ID나 `block_index`를 정렬해서 읽기 순서를 재구성하지 않습니다.** 현재 로더는 같은 페이지 안에서 입력 JSONL의 순서를 유지합니다.

### source_locator의 내부 필드

| 필드 | 자료형 | 현재 값·의미 |
|---|---|---|
| `type` | string | `pdf_page_bbox`: PDF 페이지의 사각형 위치로 출처 표현 |
| `pdf_page` | integer | 해당 블록의 PDF 페이지. 최상위 `pdf_page`와 일치 |
| `bbox` | number[4] | `[x0, y0, x1, y1]`: 블록 영역의 좌상단·우하단 좌표 |
| `units` | string | `pt`: 포인트 단위, 1pt = 1/72인치 |
| `coordinate_space` | string | `pymupdf_unrotated_page`: 회전 전 페이지 좌표계 |

PyMuPDF에서는 페이지 좌상단이 원점이며 y가 아래로 증가합니다. `bbox`는 이미지 픽셀 좌표가 아니므로 화면 표시 시 크기·회전을 반영한 변환이 필요합니다. [좌표 설명](https://pymupdf.readthedocs.io/en/latest/app3.html#coordinates), [회전 전 페이지 좌표 설명](https://pymupdf.readthedocs.io/en/latest/page.html#modifying-pages)

<details>
<summary>실제 blocks 레코드 예시: PDF 5쪽의 블록 13</summary>

```json
{
  "doc_id": "doc_0530d100e08517b6",
  "version_sha256": "0530d100e08517b60bb2acb9504d7ead70107f0c23a586738fb37e2191d1add8",
  "block_id": "doc_0530d100e08517b6:p0005:b0013",
  "pdf_page": 5,
  "block_index": 13,
  "kind": "text",
  "text": "1. 국가과학기술자문회의법 제2조에 따라 국가과학기술자문회의가 심의･의결하는 \n",
  "source_locator": {
    "type": "pdf_page_bbox",
    "pdf_page": 5,
    "bbox": [
      172.44000244140625,
      379.78192138671875,
      521.80224609375,
      392.2799987792969
    ],
    "units": "pt",
    "coordinate_space": "pymupdf_unrotated_page"
  },
  "extraction_method": "native_text",
  "verification_status": "unverified"
}
```

</details>

## 2. 실제 예시: 문장 두 조각을 하나의 청크로 연결

PDF 5쪽에서 다음 두 블록이 추출되어 있습니다. 아래 문자열은 원래 공백과 줄바꿈을 그대로 표시합니다.

| 원문 블록 | 실제 text | 문자 수 |
|---|---|---:|
| `p0005:b0013` | `"1. 국가과학기술자문회의법 제2조에 따라 국가과학기술자문회의가 심의･의결하는 \n"` | 44 |
| `p0005:b0014` | `"주요정책 및 계획의 수립･조정\n"` | 17 |

청킹 후 두 블록은 `doc_0530d100e08517b6:c_9ab883bbd62e06e1c4f3`라는 청크로 연결됩니다.

```text
p0005:b0013 ─┐
             ├─→ 청크 1개: 62문자
p0005:b0014 ─┘
```

두 원문 문자열 사이에 줄바꿈 하나를 추가하므로 `44 + 1 + 17 = 62`문자입니다. 이 예시에서는 같은 문장의 앞부분과 뒷부분을 함께 검색할 수 있게 됩니다.

## 3. chunks.jsonl: 검색할 내용과 원문 연결 정보

청크는 **검색할 텍스트와, 그 텍스트의 원문 출처를 함께 묶은 레코드**입니다. 여러 블록이 한 청크가 될 수 있고, 긴 블록의 분할이나 중첩으로 같은 블록이 여러 청크에 연결될 수도 있습니다.

### 3.1 문서·데이터 식별 필드

| 필드 | 자료형 | 예시 | 의미·활용 |
|---|---|---|---|
| `schema_version` | string | `rag-chunk/1` | 청크 JSON의 구조 버전. 읽는 코드의 호환성 확인 |
| `chunk_id` | string | `doc_...:c_9ab883bbd62e06e1c4f3` | 검색 결과를 식별하고 원래 청크를 찾는 키 |
| `doc_id` | string | 원문 블록과 같은 문서 ID | 검색 대상 문서 필터링·출처 연결 |
| `version_sha256` | string | 원본 PDF SHA-256 | 다른 문서 버전의 근거가 섞이지 않도록 확인 |
| `document_title` | string 또는 null | `kistep_2025_plan` | 표시용 제목. 현재는 원본 파일명에서 생성한 값 |
| `title_verified_by_human` | boolean | `false` | 사람이 제목을 확인했는지 표시. 현재 예시의 제목은 확인 전 상태 |
| `source_url` | string 또는 null | 공식 자료 다운로드 URL | 원본 자료에 접근할 링크. 미등록 시 null |

`chunk_id`는 문서·버전, 추출 지문, 청킹 설정·버전, 원문 구간과 내용을 반영해 생성합니다. 같은 입력·설정이면 재현되며, 청킹 조건이 달라지면 기존 검색 인덱스와 결과 참조의 갱신 여부를 확인해야 합니다.

### 3.2 텍스트·원문 연결 필드

| 필드 | 자료형 | 의미·활용 |
|---|---|---|
| `pdf_pages` | integer[] | 연결된 PDF 페이지 목록. 현재 구현은 페이지를 넘지 않아 청크마다 한 페이지 |
| `source_block_ids` | string[] | 청크를 구성한 블록 ID 목록. 원문 블록을 조회하거나 공유 근거 중복을 확인 |
| `source_spans` | object[] | 각 블록의 어느 문자 구간을 사용했는지와 원문 위치를 보존 |
| `text` | string | 인용·확인용 텍스트. 원문 블록 문자열 또는 그 일부를 줄바꿈으로 연결 |
| `search_text` | string | Unicode NFKC·공백 정규화를 적용한 검색용 문자열 |
| `char_count` | integer | `text`의 문자 수. 공백·줄바꿈 포함, 토큰 수나 검색 점수와는 다름 |

이번 예시의 차이는 다음과 같습니다.

```json
{
  "text": "1. 국가과학기술자문회의법 제2조에 따라 국가과학기술자문회의가 심의･의결하는 \n\n주요정책 및 계획의 수립･조정\n",
  "search_text": "1. 국가과학기술자문회의법 제2조에 따라 국가과학기술자문회의가 심의・의결하는 주요정책 및 계획의 수립・조정",
  "char_count": 62
}
```

`text`는 원문 표현 확인에 사용하고, BM25는 우선 `search_text`를 토큰화해 색인하는 방안을 사용합니다. `search_text`도 완성된 토큰 목록은 아닙니다. 형태소 분석·불용어 정책 등은 검색 단계에서 별도로 정합니다.

NFKC는 일부 문자 표기도 바꿉니다. 이 예시에서는 `･`가 `・`로 정규화되었습니다. 인용할 때는 `text`와 원문 위치를 기준으로 확인합니다.

### 3.3 생성 조건·추적 필드

| 필드 | 자료형 | 현재 값·의미 |
|---|---|---|
| `verification_status` | string | `unverified`: 청크 생성만으로 사실 검증 완료가 되지 않음 |
| `chunker_version` | string | `page-block-packer/1`: 사용한 청킹 알고리즘 버전 |
| `chunking_config` | object | 최대 길이·중첩·제목 경계 설정 |
| `extraction_fingerprint` | string | pages/blocks 파일 해시와 파서 이름·버전을 반영한 지문. 같은 PDF에서 서로 다른 추출 결과를 구분 |
| `notes` | string[] | 분할 과정의 참고 사항. 현재 예시에서는 빈 배열 |

현재 `chunking_config`는 다음과 같습니다.

```json
{
  "max_chars": 1000,
  "overlap_chars": 120,
  "break_on_headings": true
}
```

- `max_chars`: 청크 최대 길이 1,000문자. 토큰 수가 아닙니다.
- `overlap_chars`: 최대 120문자 범위에서 완전한 후미 블록을 반복합니다. 실제 중첩은 0일 수도 있습니다.
- `break_on_headings`: 일부 명확한 제목 표현에서 경계를 나누는 규칙을 사용합니다. 절 구분의 정확성을 보장하는 값은 아닙니다.

긴 블록이 분할되면 `notes`에 `oversized_block_split`, 문자 기준 강제 분할이 발생하면 `hard_character_split`이 남을 수 있습니다. 이는 생성 과정의 표시이며 신뢰도 점수가 아닙니다. 현재 실제 데이터에는 두 경우가 발생하지 않았습니다.

<details>
<summary>실제 chunks 레코드 전체 예시: 위의 두 블록을 묶은 청크</summary>

```json
{
  "schema_version": "rag-chunk/1",
  "chunk_id": "doc_0530d100e08517b6:c_9ab883bbd62e06e1c4f3",
  "doc_id": "doc_0530d100e08517b6",
  "version_sha256": "0530d100e08517b60bb2acb9504d7ead70107f0c23a586738fb37e2191d1add8",
  "document_title": "kistep_2025_plan",
  "title_verified_by_human": false,
  "source_url": "https://www.kistep.re.kr/boardDownload.es?bid=0017&list_no=16371&seq=2",
  "pdf_pages": [
    5
  ],
  "source_block_ids": [
    "doc_0530d100e08517b6:p0005:b0013",
    "doc_0530d100e08517b6:p0005:b0014"
  ],
  "source_spans": [
    {
      "block_id": "doc_0530d100e08517b6:p0005:b0013",
      "pdf_page": 5,
      "start_char": 0,
      "end_char": 44,
      "source_locator": {
        "type": "pdf_page_bbox",
        "pdf_page": 5,
        "bbox": [
          172.44000244140625,
          379.78192138671875,
          521.80224609375,
          392.2799987792969
        ],
        "units": "pt",
        "coordinate_space": "pymupdf_unrotated_page"
      }
    },
    {
      "block_id": "doc_0530d100e08517b6:p0005:b0014",
      "pdf_page": 5,
      "start_char": 0,
      "end_char": 17,
      "source_locator": {
        "type": "pdf_page_bbox",
        "pdf_page": 5,
        "bbox": [
          186.95999145507812,
          398.9819030761719,
          322.4695129394531,
          411.47998046875
        ],
        "units": "pt",
        "coordinate_space": "pymupdf_unrotated_page"
      }
    }
  ],
  "text": "1. 국가과학기술자문회의법 제2조에 따라 국가과학기술자문회의가 심의･의결하는 \n\n주요정책 및 계획의 수립･조정\n",
  "search_text": "1. 국가과학기술자문회의법 제2조에 따라 국가과학기술자문회의가 심의・의결하는 주요정책 및 계획의 수립・조정",
  "char_count": 62,
  "verification_status": "unverified",
  "chunker_version": "page-block-packer/1",
  "chunking_config": {
    "max_chars": 1000,
    "overlap_chars": 120,
    "break_on_headings": true
  },
  "extraction_fingerprint": "90c346ca159b642877ca1d1bf9aff551f808e07b218285e59553e4da4b0bd8a4",
  "notes": []
}
```

</details>

## 4. source_spans를 통해 정확히 어떤 원문을 썼는지 확인

| 내부 필드 | 의미 |
|---|---|
| `block_id` | 참조하는 원문 블록 ID |
| `pdf_page` | 그 블록이 있는 PDF 페이지 |
| `start_char` | 원문 블록 문자열에서 사용 구간의 시작 인덱스. 0부터 시작 |
| `end_char` | 사용 구간의 끝 인덱스. 해당 위치의 문자는 미포함 |
| `source_locator` | 원문 블록의 페이지·bbox·좌표계 정보 |

예시의 첫 span은 블록 13의 `text[0:44]`, 두 번째는 블록 14의 `text[0:17]`을 사용합니다. 두 구간 사이에 줄바꿈을 추가하면 청크의 `text`가 정확히 복원됩니다.

```text
blocks[블록 13].text[0:44]
              + "\n"
blocks[블록 14].text[0:17]
              ↓
chunk.text
```

인덱스는 Python 문자열의 문자 위치이며 UTF-8 바이트 위치가 아닙니다. 또한 `source_locator.bbox`는 **원본 블록 전체의 영역**입니다. 긴 블록의 일부만 사용해도, 그 일부 문자만 둘러싸는 정밀 좌표가 새로 계산되는 것은 아닙니다.

## 5. 팀에서 활용하는 방법

| 담당·단계 | 주로 읽는 필드 | 활용 예시 |
|---|---|---|
| RAG: BM25 | `search_text`, `chunk_id` | 검색 문자열을 토큰화하고, 결과 순서를 원래 청크에 연결 |
| RAG: 임베딩 검색 | 검색용 텍스트와 청크 ID | 청크를 벡터화하고, 원문 참조는 메타데이터로 보관 |
| Agent: 답변 생성 | 선택한 청크의 `text`, `pdf_pages`, 문서·블록 ID | 근거 문장을 읽고 출처가 연결된 답변 작성 |
| 원문 확인 화면 | `source_spans` → `source_locator` | 해당 PDF 페이지로 이동하고 블록 영역을 표시 |
| 검색 평가 | 정답 근거의 블록 ID·문자 구간 | 필요한 근거가 검색 청크 안에 포함되었는지 확인 |
| 하네스 연결 | 선택 결과의 청크 ID·문서 버전·출처·설정 참조 | 실행 결과와 사용한 근거를 추적할 인터페이스에 활용 |

검색 결과에서 청크 ID를 받은 뒤 해당 레코드를 조회하면, 원문 텍스트와 페이지·블록 참조를 함께 확보할 수 있습니다. 파일 전체를 매번 Agent에 보내는 방식이 아니라, 선택된 근거와 필요한 출처 정보를 전달하는 방향으로 연결합니다.

검색 점수는 **질문마다 달라지는 실행 결과**이므로 현재 `chunks.jsonl`에는 없습니다. 임베딩 벡터도 아직 포함되어 있지 않습니다. 이후 결과에는 `chunk_id`와 점수 등을 연결해 별도로 관리할 수 있습니다.

## 6. 활용 시 주의할 점

- 원문 문자열·좌표를 보존했다는 것은 인식·읽기 순서·표 의미가 맞다는 보증은 아닙니다. 두 파일의 현재 검증 상태는 `unverified`입니다.
- 표 헤더 계층·셀의 행/열·수치 신뢰도는 현재 스키마에 없습니다. 표가 텍스트에 포함되어 있어도 구조화된 표 데이터라고 가정하지 않습니다.
- 중첩된 청크가 같은 블록을 공유할 수 있습니다. 답변 근거나 숫자 집계에 사용할 때 같은 원문을 중복 인용·합산하지 않도록 확인합니다.
- `pdf_page`/`pdf_pages`는 파일 페이지입니다. 페이지에 인쇄된 쪽번호는 `pages.jsonl`의 관련 정보와 구분합니다.
- 향후 Parser/VLM 담당자의 출력과 연결할 때 문서·버전·블록 ID, 페이지·좌표의 의미를 함께 합의해야 합니다. 이 문서는 현재 데모의 구현 기준입니다.

## 7. 현재 파일 위치와 관련 코드

```text
data/processed/pymupdf_demo/kistep_2025_plan_0530d100e085/blocks.jsonl
data/processed/rag/kistep_2025_plan_0530d100e085/3382f591c928/chunks.jsonl
```

관련 코드: `pdf_extract.py`의 `TextBlock`/추출 함수, `rag_data.py`의 `load_document()`/`build_chunks()`/`validate_chunks()`.
실행 방법: `docs/rag-data-preparation.md`.

팀 논의 항목: 토큰화 정책, 검색 결과 반환 필드, Parser/VLM 공통 출처 계약.
