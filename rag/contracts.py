"""
검색 알고리즘이 아니라, 노드들이 주고받을 데이터 형식을 정의한다.

현재 BM25는 이런 결과를 반환한다.
'청크 ID' + '순위' + '점수' + '원본 청크'

N-02는 `chunk_id`를 받고자 하는데, N-01이 `id`라는 이름으로 보내면 연결되지 않는다.
때문에 필드명/자료형/필수 여부를 공통으로 정의한다.

*여기서는 검색 요청,후보,결과 형식을 정의하도록 한다.
"""

from dataclasses import dataclass, asdict
from typing import Generic, Literal, TypeVar, get_args

import json

NodeStatus = Literal["success", "partial", "error"]
StepStatus = Literal["success", "failed", "skipped"]

@dataclass
class DocumentRef:
    document_id: str
    document_version: str

    def __post_init__(self):
        """ 잘못된 값을 막는 단계 """

        if not isinstance(self.document_id, str):
            raise TypeError("document_id는 문자열이어야 합니다.")

        if not self.document_id.strip():
            raise ValueError("document_id는 비어 있거나, 공백뿐일 수 없습니다.")

        if not isinstance(self.document_version, str):
            raise TypeError("document_version은 문자열이어야 합니다.")

        if not self.document_version.strip():
            raise ValueError("document_version이 비어 있거나, 공백뿐입니다.")

@dataclass
class RetrievalRequest:
    """
    검색 문구 / 프로필 참조 등이 올바른 형식인가?
    """
    request_ref: str
    search_text: str
    document_scope: list[DocumentRef]
    retrieval_profile: str

@dataclass
class RetrievalCandidate:
    """
    RetrievalCandidate는 후보 한 개이다.
    """
    chunk_id: str                           # 검색된 청크 ID
    document_id: str                        # 해당 문서 ID
    document_version: str                   # 해당 문서 버전
    rank: int                               # 최종 순위, 1부터 시작
    score_metadata: dict[str, float | int]  # 검색 방식별 점수 및 순위

    def __post_init__(self) -> None:
        """ 빈 ID와 `rank=-1`을 막아야 함."""
        if not isinstance(self.chunk_id, str):
            raise TypeError("chunk_id는 문자열이어야 합니다.")
        if not self.chunk_id.strip():
            raise ValueError("chunk_id는 비어 있거나 공백뿐일 수 없습니다.")

        if not isinstance(self.document_id, str):
            raise TypeError("document_id는 문자열이어야 합니다.")
        if not self.document_id.strip():
            raise ValueError("document_id는 비어 있거나 공백뿐일 수 없습니다.")

        if not isinstance(self.document_version, str):
            raise TypeError("document_version은 문자열이어야 합니다.")

        if not self.document_version.strip():
            raise ValueError("document_version은 비어 있거나 공백뿐일 수 없습니다.")

        if type(self.rank) is not int:
            raise TypeError("rank는 정수여야 합니다.")

        if self.rank < 1:
            raise ValueError("rank는 1 이상이어야 합니다.")
            

@dataclass
class RetrievalCandidates:
    """
    본 데이터클래스는 후보 목록 전체이다.
    """
    request_ref: str                        # 원 요청 참조
    candidates: list[RetrievalCandidate]    # 후보 객체 목록
    index_refs: dict[str, str]              # 사용한 검색 색인의 버전 참조
    strategy_version: str                   # 검색 전략 버전


@dataclass(frozen=True)
class ContextExpansionConfig:
    """N-02 개발 기본값. 길이 단위는 토큰이 아닌 원문 Unicode 문자이다."""
    before_chars: int = 200
    after_chars: int = 200
    max_total_chars: int = 12000

    def __post_init__(self) -> None:
        for name in ("before_chars", "after_chars", "max_total_chars"):
            value = getattr(self, name)
            if type(value) is not int:
                raise TypeError(f"{name}은 정수여야 합니다.")
            if value < (1 if name == "max_total_chars" else 0):
                raise ValueError(f"{name}의 길이 설정이 올바르지 않습니다.")


@dataclass
class SourceSpan:
    """검색 가공문이 아닌 원본 블록의 문자 구간. end_char는 미포함이다."""
    block_id: str
    pdf_page: int
    start_char: int
    end_char: int
    text: str
    source_locator: dict
    extraction_method: str = "unknown"
    verification_status: str = "unverified"

    def __post_init__(self) -> None:
        if not isinstance(self.block_id, str) or not self.block_id.strip():
            raise ValueError("원문 블록 ID가 필요합니다.")
        if type(self.pdf_page) is not int or self.pdf_page < 1:
            raise ValueError("PDF 페이지는 양의 정수여야 합니다.")
        if (type(self.start_char) is not int or type(self.end_char) is not int
                or not 0 <= self.start_char < self.end_char):
            raise ValueError("원문 문자 구간이 올바르지 않습니다.")
        if not isinstance(self.text, str) or len(self.text) != self.end_char - self.start_char:
            raise ValueError("원문 구간 길이와 텍스트 길이가 다릅니다.")
        if not isinstance(self.source_locator, dict):
            raise TypeError("원문 위치는 딕셔너리여야 합니다.")
        for value in (self.extraction_method, self.verification_status):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("추출 방법과 검증 상태는 비어 있지 않은 문자열이어야 합니다.")


@dataclass
class EvidenceAnchor:
    """확장·병합된 근거가 어느 검색 후보에서 시작했는지 보존한다.

    여러 페이지 청크는 각 페이지의 원문 구간을 해당 근거 항목에 나누어 보존한다.
    """
    chunk_id: str
    rank: int
    text: str
    source_spans: list[SourceSpan]


@dataclass
class EvidenceItem:
    """같은 문서·버전·페이지 안에서 겹치는 문맥을 합친 근거 한 건."""
    evidence_id: str
    document_id: str
    document_version: str
    pdf_pages: list[int]
    text: str
    source_spans: list[SourceSpan]
    anchors: list[EvidenceAnchor]
    verification_status: str
    reconstruction_match: bool = True


@dataclass
class EvidenceBundle:
    """N-04에 전달할 근거. 검색 점수와 추출 정확도 검증을 혼동하지 않는다."""
    request_ref: str
    items: list[EvidenceItem]
    config: ContextExpansionConfig
    char_count: int
    requested_candidates: int
    resolved_candidates: int
    skipped_chunk_ids: list[str]
    budget_limited: bool
    context_strategy_version: str = "fixed-page-window-v1"

@dataclass
class DraftCitation:
    evidence_id: str                    # N-02가 만든 근거 항목 ID
    quote: str                          # 그 근거에서 '그대로' 가져온 구절

@dataclass
class DraftClaim:
    claim_id: str
    text: str
    citations: list[DraftCitation]      # 해당 주장을 뒷받침한다고 제시한 근거

@dataclass
class ReviewDraft:
    request_ref: str
    question: str
    claims: list[DraftClaim]            # 답변을 구성하는 개별 주장 목록
    missing_information: list[str]      # 질문에 답하기 위해 추가로 필요한 정보

@dataclass
class StepDiagnostic:
    status: StepStatus
    error_code: str | None = None
    reason: str | None = None
    retryable: bool | None = None
    count: int | None = None

    def __post_init__(self) -> None:
        # Literal은 타입 힌트이므로 허용값을 실행 시에도 확인한다.
        if not isinstance(self.status, str):
            raise TypeError("단계 status는 문자열이어야 합니다.")
        if self.status not in get_args(StepStatus):
            raise ValueError("단계 status는 success, failed, skipped 중 하나여야 합니다.")

        if self.count is not None:
            if type(self.count) is not int:
                raise TypeError("count는 정수 또는 None이어야 합니다.")
            if self.count < 0:
                raise ValueError("count는 0 이상이어야 합니다.")

        for name in ("error_code", "reason"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name}은 문자열 또는 None이어야 합니다.")
        if self.retryable is not None and type(self.retryable) is not bool:
            raise TypeError("retryable은 bool 또는 None이어야 합니다.")


T = TypeVar("T")                            # 업무 데이터 타입이 들어갈 자리

@dataclass
class NodeResult(Generic[T]):
    status: NodeStatus
    data: T | None
    diagnostics: dict[str, StepDiagnostic]
    outcome: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str):
            raise TypeError("노드 status는 문자열이어야 합니다.")
        if self.status not in get_args(NodeStatus):
            raise ValueError("노드 status는 success, partial, error 중 하나여야 합니다.")

        if not isinstance(self.diagnostics, dict):
            raise TypeError("diagnostics는 딕셔너리여야 합니다.")
        for name, diagnostic in self.diagnostics.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("diagnostics의 단계명은 비어 있지 않은 문자열이어야 합니다.")
            if not isinstance(diagnostic, StepDiagnostic):
                raise TypeError("diagnostics의 값은 StepDiagnostic 객체여야 합니다.")

        if self.outcome is not None and not isinstance(self.outcome, str):
            raise TypeError("outcome은 문자열 또는 None이어야 합니다.")
        # data의 업무별 타입과 상태 간 정합성은 해당 노드에서 추가 검사한다.

if __name__ == "__main__":
    document = DocumentRef(
        document_id="example_doc",
        document_version="example_version",
    )
    
    candidate = RetrievalCandidate(
        chunk_id="example_chunk",
        document_id=document.document_id,
        document_version=document.document_version,
        rank=1,
        score_metadata={
            "bm25_rank": 1,
            "bm25_score": 8.35,
        },
    )

    retrieval_data = RetrievalCandidates(
        request_ref="example_request",
        candidates=[candidate],
        index_refs={"bm25": "example_index"},
        strategy_version="example_strategy",
    )

    result = NodeResult[RetrievalCandidates](
        status="success",
        data=retrieval_data,
        diagnostics={
            "bm25_search": StepDiagnostic(
                status="success",
                count=1,
            ),
        },
        outcome=None,
    )
    print(
        json.dumps(
            asdict(result),
            ensure_ascii=False,
            indent=2,
        )
    )
    RetrievalCandidate(
        chunk_id="example_chunk",
        document_id="example_doc",
        document_version="example_version",
        rank=1,
        score_metadata={},
    )
