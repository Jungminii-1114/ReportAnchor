"""
N-01, "retrieve_evidence" is a tool for searching relevant chunks of data.(json)

여기서는 BM25의 결과를 공통 형식으로 연결한다.

RetrievalRequest
   ↓ 요청·문서 범위·버전 확인
BM25Retriever.search()
   ↓ 검색 결과를 계약 객체로 변환
RetrievalCandidates
   ↓ 실행 기록과 함께 포장
NodeResult[RetrievalCandidates]

"""

import json
from dataclasses import asdict
import argparse
from pathlib import Path

from rag.contracts import (
    DocumentRef, NodeResult, RetrievalCandidate, RetrievalCandidates,
    RetrievalRequest, StepDiagnostic,
)
from rag.retrieval.bm25 import BM25Retriever, load_chunks


def retrieve_evidence(
    request: RetrievalRequest,              # 검색 문구/허용 문서/버전/검색 프로필
    retriever: BM25Retriever,               # 이미 인덱스가 만들어진 검색 객체
    *,
    index_ref: str | None = None,           # 실제 색인 참조. None이면 아직 미연결
    top_k: int = 5,                         # 반환 후보 수
) -> NodeResult[RetrievalCandidates]:
    
    """
    BM25 검색 결과를 N-01 공통 반환 구조로 연결한다.
    개발용 연결 단계이며 요청 범위·버전 검증과 오류 처리는 아직 추가하지 않았다.
    
    1. 입력 확인        검색 문구/프로필/문서 범위/버전 확인 구현하기
    2. 검색            request.search_text로 기존 검색기 호출
    3. 후보 변환        결과를 RetrievalCandidate 목록으로 변환하기
    4. 반환            후보 묶음 및 Diagnostic 기록을 NodeResult로 반환
    """

    # 입력 검사 추가
    if (
        not isinstance(request.search_text, str)
        or not request.search_text.strip()
    ):
        return NodeResult(
            status="error",
            data=None,
            diagnostics={
                "validate_retrieval_input": StepDiagnostic(
                    status="failed",
                    error_code="INVALID_SEARCH_TEXT",
                    reason="검색 문구는 비어 있지 않은 문자열이어야 합니다.",
                    retryable=False,
                ),
            },
            outcome=None,
        )
    
    # 1. 기존 BM25 검색기를 호출한다.
    hits = search_for_request(
        request=request,
        retriever=retriever,
        top_k=top_k,
    )

    candidates = []

    for hit in hits:
        chunk = hit["chunk"]

        candidate = RetrievalCandidate(
            chunk_id=hit["chunk_id"],
            document_id=chunk["doc_id"],
            document_version=chunk["version_sha256"],
            rank=hit["rank"],
            score_metadata={
                "bm25_rank": hit["rank"],
                "bm25_score": hit["score"],
            },
        )

        candidates.append(candidate)

    retrieval_data = RetrievalCandidates(
        request_ref=request.request_ref,
        candidates=candidates,
        index_refs={"bm25": index_ref} if index_ref is not None else {},
        strategy_version=request.retrieval_profile,
    )
    
    result = NodeResult[RetrievalCandidates](
        status="success",
        data=retrieval_data,
        diagnostics={
            "bm25_search": StepDiagnostic(
                status="success",
                count=len(candidates),
            ),
        },
        outcome=None,
    )
    return result


def search_for_request(request, retriever, top_k):
    question=request.search_text
    hits = retriever.search(question, top_k=top_k)

    return hits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--chunks", type=Path, required=True)

    args = parser.parse_args()

    path = args.chunks.expanduser().resolve()
    chunks=load_chunks(path)
    retriever = BM25Retriever(chunks)

    document_scope=[]

    for chunk in chunks:
        document = DocumentRef(
            document_id = chunk["doc_id"],
            document_version=chunk["version_sha256"],
        )

        if document not in document_scope:
            document_scope.append(document)

    request = RetrievalRequest(
        request_ref="cli_demo",
        search_text=args.query,
        document_scope=document_scope,
        retrieval_profile="bm25-only-development-v1",
    )

    result = retrieve_evidence(
        request=request,
        retriever=retriever,
        top_k=args.top_k,
    )
    print(
        json.dumps(
            asdict(result),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
