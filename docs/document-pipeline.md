# 문서 처리·하이브리드 검색 파이프라인 (러프 설계)

> 상태: 구조 및 저장 데이터의 1차 설계. 기술 스택·모델·세부 파라미터·구현은 미확정.
>
> 목적: 문서군·기관·데이터셋에 독립적인 로컬 처리 구조를 만들고, 각 단계의 출력을 저장해 재사용·비교·실패 추적 가능하게 한다.
>
> 설계 경계: **파싱 → 청킹 → (Dense 임베딩 / BM25 토큰화) → 각각의 색인 → N-01 Hybrid + RRF → 선택적 Reranker → RetrievalCandidates → N-02 원문·고정 길이 문맥 조회 → EvidenceBundle**. RAG 답변 생성·사실 검증은 후속 단계로 분리한다.

## 1. 파이프라인 구조

```mermaid
flowchart TD
    P["① Parsing<br/>원문 구조 추출<br/>ParsedDocument 저장"]
    C["② Chunking<br/>구조 기반 청킹<br/>ChunkSet 저장"]
    subgraph DENSE["Dense 경로"]
        direction TB
        E["③-A Embedding<br/>벡터 생성"]
        V["④-A Vector Index<br/>벡터 검색 색인"]
        E --> V
    end
    subgraph SPARSE["Sparse / BM25 경로"]
        direction TB
        T["③-B Tokenization<br/>형태소 분석 · 토큰화"]
        B["④-B BM25 Index<br/>어휘 기반 검색 색인"]
        T --> B
    end
    Q["사용자 질문 + 검색 범위"]
    H["⑤ Hybrid Retrieval<br/>Dense + BM25 후보 조회"]
    F["RRF<br/>순위 기반 후보 통합"]
    S{"Reranker 사용?"}
    R["⑥ Reranker 모듈<br/>규칙 기반 / 로컬 모델<br/>외부 API는 실험 전용"]
    RC["RetrievalCandidates<br/>N-01 검색 후보 + 순위"]
    N2["N-02 get_source_context<br/>source_spans 조회 + 고정 길이 확장"]
    O["EvidenceBundle<br/>원문 문맥 + 출처 참조"]
    P --> C
    C --> E
    C --> T
    V --> H
    B --> H
    Q --> H
    H --> F
    F --> S
    S -->|"아니요: 구성 A"| RC
    S -->|"예: 구성 B"| R
    R --> RC
    RC --> N2
    N2 --> O
```

### 다이어그램 읽는 법

**문서 준비 (①부터 ④까지)**

문서를 파싱하고 검색 가능한 청크로 나눈다. 같은 `ChunkSet`을 Dense와 BM25 경로에 전달한 뒤, 두 검색 색인을 각각 생성·저장한다. 문서가 추가되거나 내용이 바뀌었을 때 필요한 부분만 다시 처리한다.

**사용자 질문 처리 (⑤부터 ⑥까지)**

질문이 들어오면 Dense와 BM25에서 후보를 검색한다. **RRF**로 두 검색 결과의 순위를 통합한 후, 필요하면 **Reranker**로 후보의 관련성을 다시 평가한다. N-01은 어느 구성에서나 `RetrievalCandidates`를 만들고, N-02가 **원문 위치 범위(`source_spans[]`)를 조회하고 고정 길이 문맥을 확장**해 `EvidenceBundle`로 전달한다.

**비교할 실행 구성**

| 구성 | 검색 흐름 | 확인할 사항 |
|---|---|---|
| A. RRF 단독 | Dense + BM25 → RRF → EvidenceBundle | 추가 리랭커 없는 검색 품질·지연·자원 사용량 |
| B. Reranker 추가 | Dense + BM25 → RRF → Reranker → EvidenceBundle | 재정렬에 따른 품질 개선과 추가 비용 |

**A와 B 중 어느 구성을 실제 제품의 기본값으로 사용할지는 비교 실험 후 결정한다.** Reranker는 규칙 기반, 로컬 Cross-Encoder 등 여러 구현으로 교체할 수 있다. Voyage API는 외부 평가 베이스라인을 위한 실험 아이디어이며, 로컬 제품의 필수 기능이 아니다.

**용어 구분**

- **RRF (Reciprocal Rank Fusion):** Dense와 BM25의 원점수를 직접 합산하지 않고, 검색 결과의 **순위**를 결합한다.
- **PRF (Pseudo-Relevance Feedback):** 초기 상위 검색 결과를 활용해 질문을 확장하고 **다시 검색**하는 별도 방식이다. 현재 설계에는 포함하지 않는다.
- **BM25 토큰화:** 한국어 형태소 분석 등을 활용할 수 있지만, 형태소 분석이 반드시 필요한 것은 아니다. 학습 기반 Sparse Embedding을 도입한 상태도 아니다.

## 2. 입력 (문서 파일)

| 구분 | 입력 | 처리 원칙 |
|---|---|---|
| 기본 문서 파일 | PDF, HWP 및 지원이 검증된 추가 형식(HWPX 등) | 형식별 파서를 교체 가능하게 구성. 실제 지원 범위는 파서 선정 후 확정 |
| 내용 | 본문, 표, 이미지, 차트, 도형, 수식, 레이아웃 | 추출할 수 없는 요소도 원문 위치·미지원/미검증 상태를 가급적 보존 |
| 문서 메타데이터 | 원본 파일 참조, 확인한 형식, 파일 크기, 내용 해시 | 파일명이나 특정 기관·분야 이름을 로직 분기 조건으로 사용하지 않음 |
| 선택적 처리 설정 | 파서·청킹·토큰화·임베딩·색인 설정 | 문서 내용과 독립된 설정 및 버전으로 기록 |

원문은 로컬에서 관리한다. 모델별·색인별 반복 처리를 위해 동일 파일을 임의로 복제하거나 원문 텍스트를 모든 실행 로그에 중복 기록하지 않는다.

**질의 시 추가 입력:** 검색 질문(`query`), 사용자가 허용한 문서/버전 범위, 선택적 검토 기준. 문서 등록 입력과 혼동하지 않는다.

## 3. 출력 및 저장 (저장할 데이터)

| 단계 | 산출물(개념명) | 저장할 필수 데이터 | 선택적·후속 데이터 |
|---|---|---|---|
| ① Parsing | `DocumentManifest`, `ParsedDocument` | `document_id`, 원문 해시·형식·버전, `block_id`, 요소 종류·순서·추출 내용, 원문 위치, 파서·설정 버전, 추출 상태 | TableIR(셀·병합·헤더 추정), 이미지/도형 영역 참조, OCR·VLM 보완 결과·검증 상태 |
| ② Chunking | `ChunkSet` | `chunk_id`, `document_id`, 문서 버전, 원문 블록별 `source_spans[]`(`block_id`, `start_offset`, `end_offset`), 청크 원문/검색용 표현, 청킹 설정·버전, 입력·출력 해시 | 표의 행 라벨·상위 열 헤더·각주 연결, 색인용/LLM용/검증용 표현 구분 |
| ③-A Embedding | `EmbeddingArtifact` | `chunk_id`, 벡터 데이터 또는 `vector_ref`, 임베딩 모델 ID·정확한 버전, 차원, 입력 텍스트 해시, 전처리·정규화·지시문 설정 | 배치 ID, 연산 장치, 생성 시간 및 자원 사용량 |
| ④-A Vector Index | `VectorIndexManifest` | 색인 버전·저장 위치, 임베딩 집합 해시, 벡터 차원, 거리 함수/인덱스 설정, `chunk_id` 대응 관계 | 인덱스별 성능·메모리 측정 |
| ③-B Tokenization | `LexicalCorpus` | `chunk_id`, 원본 검색 텍스트 참조, 분석·정규화된 토큰 목록, 토큰화 규칙·분석기/사전 버전, 입력 해시 | 품사 정보, 토큰 위치, 숫자·영문·기호 보존 관련 진단 |
| ④-B BM25 Index | `BM25IndexManifest` 및 실제 색인 | 색인 버전·저장 위치, 토큰화된 코퍼스 해시, `chunk_id` 매핑, BM25 구현·파라미터(`k1`, `b` 등), 색인 건수 | 문서별 TF·길이, 토큰별 DF/IDF, 평균 문서 길이 등 통계(엔진이 관리할 수도 있음) |
| ⑤ Hybrid + RRF (공통) | `FusionResult` / `RetrievalCandidates` | 질의 ID·해시, 대상 문서/색인 버전, 후보 `chunk_id`, Dense·BM25 개별 순위·점수, RRF 순위·설정, 후보 집합 해시 | 검색 엔진·질의 처리 시간, 후보별 원문 위치 |
| ⑥ Reranker (선택) | `RerankResult` | 입력 후보 집합의 해시·순서, 구현/모델 ID·버전, 재평가 점수·최종 순위, `chunk_id`, 실행·오류·건너뜀 상태 | 규칙·지시문·입력 표현 버전, 토큰·지연·비용, 외부 API 여부 |
| N-02 원문 근거 조회 | `NodeResult[EvidenceBundle]` | `request_ref`, `context_profile_ref`, `evidence_items[]`의 `chunk_id`·문서 ID/버전·검색 순위·`context_text`·`source_refs[]`·`context_truncated`, 실행 진단 | 구조 기반 제목·표 헤더·각주 연결(추후 개선), 사실성·근거 충분성 검증(후속 노드) |

**공통 저장 규칙:**
- 데이터가 큰 원문·이미지·벡터·색인 본체는 별도 로컬 저장소에 보관하고, 하네스 실행 상태에는 **아티팩트 ID/버전/해시/위치 참조**를 기록한다.
- **MVP에 구현할 저장 기능은 H-02 단계별 아티팩트 캡처**다. **체크포인트/중단 후 재개(H-03)는 미정(미구현 가능)**이고, 아티팩트의 필수 의존성이 아니다. Trace는 별도의 관측 개념이다. [H-02 구현 범위](artifact-capture.md)
- `chunk_id`는 양쪽 색인에서 동일한 원본 청크를 가리킨다. `ChunkSet.source_spans[]`는 청킹에 사용한 **`ExtractedBlock.raw_content`의 블록별 위치**를 나타내며, **0부터 시작하는 유니코드 코드 포인트 인덱스**와 **시작 포함·끝 제외 `[start_offset, end_offset)`**를 MVP 기준으로 확정한다. 청크가 여러 블록에 걸치면 범위를 원문 순서대로 기록한다. 정규화한 검색 텍스트의 위치와 혼용하지 않는다. N-02는 청킹 당시와 **동일한 파싱 결과 스냅샷(원문 블록·파서/설정 버전·해시/아티팩트 참조)**을 조회해야 하며, 불일치 시 임의로 재매핑하지 않는다. 페이지·좌표 등 `source_locator`는 문자열 오프셋과 별도로 보존한다.
- RRF 결과를 기준선으로 항상 보존하고, Reranker 결과는 별도 아티팩트로 관리한다. **리랭커가 첫 검색에서 누락된 문서를 새로 발견하는 것은 아니므로** 최초 후보 집합과 최종 순위를 구별한다.
- 검색 엔진 내부 통계가 충분히 영속화되는 경우 TF/DF를 별도 파일로 중복 저장할 필요는 없다. **재현 가능한 토큰화 설정·코퍼스 스냅샷·색인 버전**을 확보하는 것이 목적이다.
- 내용 기반 캐시는 입력 해시와 관련 모델·분석기·설정·스키마 버전으로 유효성을 판단한다. 문서명이 같다는 이유만으로 재사용하지 않는다.
- 특정 라이브러리나 데이터셋 전용 스키마에 의존하지 않으며, 로컬·상업적 사용 및 오픈소스 재배포 적합성은 선정 시 별도 확인한다.

## 4. 단계별 설명

### 4.1 문서 준비

**① Parsing — 원문 구조 추출**

PDF·HWP 등에서 본문, 표, 원문 위치, 시각적 요소를 추출한다. **추출 성공과 내용의 정확성은 구분**하며, 파서가 달라도 같은 출력 계약을 유지한다. 자세한 내용은 [파싱 단계 설계](parsing-stage.md)를 참고한다.

**② Chunking — 검색 단위 생성**

문단·제목·표의 구조를 고려해 청크를 만든다. 표에서는 행 라벨, 상위 헤더, 값, 각주의 연결 관계를 가능한 한 보존한다. **각 `chunk_id`에 원문 블록별 `source_spans[]`를 저장해 N-02가 검색 문구 재탐색 없이 원문 위치를 찾도록 한다. 오프셋 기준은 `raw_content`의 코드 포인트 인덱스·`[start, end)`이며, 청킹 당시 파싱 아티팩트 버전과 함께 묶는다.** Dense와 BM25에 서로 다른 검색용 텍스트를 제공하더라도 같은 원문 `chunk_id`로 연결한다.

### 4.2 두 검색 색인 준비

**③-A Embedding → ④-A Vector Index (Dense 경로)**

청크의 검색용 텍스트를 로컬 임베딩 모델로 벡터화하고 색인한다. 모델이나 임베딩 설정만 바뀌었다면 기존 파싱·청킹 결과는 재사용한다.

**③-B Tokenization → ④-B BM25 Index (어휘 검색 경로)**

청크를 토큰화해 BM25 색인을 만든다. 한국어 형태소 분석기를 사용할 수 있지만, 숫자·영문 약어·부정 표현이 소실되지 않는지도 확인해야 한다. 분석기만 교체하면 Dense 벡터는 다시 만들지 않는다.

### 4.3 사용자 질문 처리

**⑤ Hybrid Retrieval + RRF — 후보 검색·통합**

사용자 질문을 두 검색 경로에 전달하고, 동일한 `chunk_id`를 기준으로 중복을 정리한 뒤 RRF로 순위를 결합한다. RRF는 **검색 순위**를 사용하므로 서로 다른 척도의 Dense·BM25 원점수를 직접 더하지 않는다.

**⑥ Reranker — 선택적 후보 재평가**

RRF 상위 후보의 텍스트와 사용자 질문을 비교해 관련성 순위를 다시 매긴다. 규칙 기반 방식이나 로컬 모델을 교체형으로 연결한다. **리랭커의 관련성 점수는 원문 수치나 인용이 사실이라는 검증 결과가 아니다.**

**EvidenceBundle — Agent에 근거 전달**

N-01은 검색·재순위화한 후보를 `RetrievalCandidates`로 반환하고, N-02 `get_source_context`가 원문 문맥을 조회하여 `EvidenceBundle`을 구성한다. **N-02의 MVP 문맥 확장은 고정 길이 방식**으로 확정했다. 가능한 원문 위치·기존 메타데이터는 보존하되, 표·헤더·각주 간 구조적 관계를 이용한 확장은 추후 개선 후보로 남긴다([N-02 상세](small-nodes/N-02.md)). RRF 결과와 리랭커 결과의 참조는 구별해 보존한다.

## 5. 변경 시 재사용·무효화 범위

| 변경 항목 | 다시 처리해야 하는 대표 단계 |
|---|---|
| 파서·원문 내용 변경 | 변경된 파싱 결과 및 해당 결과에 의존하는 청킹·두 검색 경로 |
| 청킹 전략·검색 표현 변경 | 변경된 청크에 대해 두 검색 경로의 토큰·임베딩 및 색인 |
| 형태소 분석기·사전/정규화 규칙 변경 | Tokenization → BM25 Index |
| 임베딩 모델·차원/입력 설정 변경 | Embedding → Vector Index |
| BM25 `k1`/`b`, 검색 색인 설정 변경 | 해당 BM25 색인 또는 질의 설정(엔진 구현에 따라 다름) |
| RRF 상수·가중치 변경 | 저장된 Dense/BM25 검색 결과의 순위 재결합(후보가 같다면 원문 재색인 불필요) |
| Reranker 모델·규칙·설정 변경 | 저장된 RRF 후보를 입력으로 재평가. 인덱스 재구축 불필요 |

개별 검색 점수의 척도가 달라 Dense 유사도와 BM25 점수를 그대로 더하지 않는다. **RRF는 원점수 대신 순위를 결합하는 공통 후보 통합 방식**이다. 최종 구성은 리랭커 유무와 종류를 포함한 비교 실험 결과로 결정한다.

## 6. Reranker 비교 설계 및 외부 기준선 아이디어

### 6.1 비교할 두 실행 구성

| 구성 | 실행 경로 | 의도 |
|---|---|---|
| **A. RRF 단독** | Hybrid(Dense+BM25) → **RRF** → EvidenceBundle | 추가 리랭커 없는 완전 로컬 기준선 |
| **B. 리랭커 추가** | 동일 Hybrid + RRF 후보 → **선택한 Reranker 구현** → EvidenceBundle | 재정렬 방식에 따른 정확도·지연·메모리·비용을 비교 |

Reranker는 공통 실행 단계가 아니다. 실패·시간 초과·비활성화 시에는 오류를 기록하고, 운영 정책이 허용하면 **RRF 단독 결과로 폴백**한다. 로컬 환경·민감 문서·기업 배포에는 외부 API 사용을 요구하지 않는다.

### 6.2 Voyage reranker 외부 평가 베이스라인 — 아이디어 / 미확정

**목적:** 상용 API Reranker의 결과를 외부 **품질 비교 기준점(reference)** 으로 설정하고, 향후 로컬 Reranker 후보가 도달할 검색 품질·비용 대비 성능을 비교한다. Voyage 점수를 정답(label)으로 취급하거나 로컬 모델이 동일 순위를 반드시 재현해야 한다고 가정하지 않는다.

- [Voyage 공식 Reranker 문서](https://docs.voyageai.com/docs/reranker)는 질문과 후보 문서를 받아 관련성 순위·점수를 반환하는 API를 안내한다. 사용 가능한 `rerank-2.5` 계열과 신규 모델 등 **실험 시점의 이용 가능한 정확한 모델명·버전**을 확인하고 고정한다.
- 모델별 응답 구조·토큰 한도·비용·속도는 [API 문서](https://docs.voyageai.com/reference/reranker-api)와 [요금표](https://docs.voyageai.com/docs/pricing)를 기준으로 실험 직전에 재확인한다.
- **공정 비교 원칙:** 동일한 질문, 문서 버전, 청킹·색인 버전, **동일 RRF 후보 집합/표현 및 입력 순서**, 동일 최종 Top-k를 사용하여 (A) 리랭킹 없음, (B-1) Voyage API, (B-2) 로컬 후보 등을 비교한다.
- 별도의 원문 검증된 평가 정답이 있을 때 `nDCG@k`, `MRR@k`, `Recall@k` 등을 평가한다. 특히 Recall은 초기 후보군의 상한에 제약받는다. 관련성 정확도뿐 아니라 **지연시간, 토큰·API 비용, 로컬 메모리, 실패율**도 기록한다.
- 저장할 정보 후보: `eval_run_id`, `query_id`, `candidate_set_hash`, `chunk_id`별 입력·RRF 순위, 모델 제공자·버전·입력 설정, 리랭킹 순위·점수, `top_k`, 사용 토큰·시간·비용, 오류·재시도, 평가 데이터셋 버전.
- **외부 데이터 전송 주의:** Voyage API에 질문·후보 문서 내용이 전송되므로 **공개 자료·합성 자료·명시적으로 전송 허가받은 자료로만** 실험한다. 기업 내부·개인정보·비밀 문서는 외부 전송하지 않는다.
- **약관 확인 필수:** [Voyage Terms of Service](https://www.voyageai.com/tos)는 별도 opt-out 전 고객 콘텐츠의 서비스 개선·모델 학습 활용 권한을 규정한다. opt-out은 이후 제출 데이터에만 적용하며 무료 크레딧·토큰 자격에 영향을 줄 수 있다고 안내한다. 실험 계정의 설정과 계약을 확인한다.
- **오픈소스·로컬 요구 조건:** Voyage API는 **실험 및 비교 전용**이며 제품 기본 설치·런타임 의존성, 기업 오프라인 기능의 필요 조건으로 두지 않는다. 서비스 약관은 오픈소스 모델 가중치 라이선스와 별개다.
- 현재 **품질·정확도 목표치/사용 모델 확정 및 실측은 하지 않았다.** 실험 자체도 아직 수행하지 않은 계획 아이디어다.

### 6.3 추후 별도 설계 대상

- 검색 요청·문서 범위 필터 계약 및 후보 Top-k 정책
- 표·각주·문단의 원문 문맥 확장 위치(리랭커 입력 전/후)
- RRF와 Reranker 점수·순위를 기록하는 공통 출력 계약
- 외부 비교 실험 격리, 평가 세트 독립성, 데이터 보안·요금 제한
- Reranker 이후 EvidenceBundle을 생성·검증 Agent에 전달하는 구조

## 참고

- [하네스 아이디어 백로그](idea-backlog.md)
- [파싱 단계 설계](parsing-stage.md)
- [기술 레퍼런스 목록](reference-catalog.md)
- [Elasticsearch: Reciprocal rank fusion](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion)
- [Qdrant: Hybrid and multi-stage queries](https://qdrant.tech/documentation/search/hybrid-queries/)
- [Voyage Reranker 공식 가이드](https://docs.voyageai.com/docs/reranker)
- [Voyage API·요금표](https://docs.voyageai.com/reference/reranker-api) · [요금](https://docs.voyageai.com/docs/pricing)
- [Voyage 서비스 약관](https://www.voyageai.com/tos)
