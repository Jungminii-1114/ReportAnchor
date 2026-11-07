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
import hashlib
from dataclasses import asdict
import argparse
from pathlib import Path

from rag.contracts import (
    DocumentRef, NodeResult, RetrievalCandidate, RetrievalCandidates,
    RetrievalRequest, StepDiagnostic,
)
from rag.retrieval.bm25 import BM25Retriever, load_chunks


def retrieve_evidence(
    request: RetrievalRequest,
    retriever: BM25Retriever,
    *,
    index_ref: str | None = None,
    top_k: int = 5,
) -> NodeResult[RetrievalCandidates]:
    def failed(step, code, reason):
        return NodeResult('error', None, {
            step: StepDiagnostic('failed', code, reason, False),
        }, None)

    if not isinstance(request, RetrievalRequest):
        return failed('validate_retrieval_input', 'INVALID_REQUEST', '검색 요청의 형식이 올바르지 않습니다.')
    if not isinstance(request.search_text, str) or not request.search_text.strip():
        return failed('validate_retrieval_input', 'INVALID_SEARCH_TEXT', '검색 문구는 비어 있지 않아야 합니다.')
    if (not isinstance(request.request_ref, str) or not request.request_ref.strip()
            or not isinstance(request.retrieval_profile, str) or not request.retrieval_profile.strip()
            or type(top_k) is not int or top_k < 1
            or not isinstance(request.document_scope, list)):
        return failed('validate_retrieval_input', 'INVALID_REQUEST', '요청 ID·범위·프로필·Top-k를 확인하세요.')
    try:
        for ref in request.document_scope:
            if not isinstance(ref, DocumentRef):
                raise ValueError('잘못된 문서 범위입니다.')
            ref.__post_init__()
        for chunk in retriever.chunks:
            chunk_identity(chunk)
        known_pairs = {chunk_identity(chunk) for chunk in retriever.chunks}
        requested_pairs = {(ref.document_id, ref.document_version) for ref in request.document_scope}
        if not requested_pairs <= known_pairs:
            raise ValueError('요청한 문서·버전을 현재 색인에서 확인할 수 없습니다.')
        if index_ref is not None and (not isinstance(index_ref, str) or not index_ref.strip()):
            raise ValueError('잘못된 색인 참조입니다.')
        if index_ref is None:
            snapshot = dict(chunks=retriever.chunks, tokenizer=retriever.tokenizer_version,
                            k1=retriever.index.k1, b=retriever.index.b,
                            epsilon=retriever.index.epsilon)
            digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                               allow_nan=False).encode()).hexdigest()
            index_ref = 'bm25-runtime-sha256:' + digest
    except (ValueError, TypeError, KeyError, AttributeError):
        return failed('validate_retrieval_input', 'INVALID_INDEX_OR_SCOPE', '문서 범위와 색인 내용을 확인하세요.')
    try:
        hits = search_for_request(request, retriever, top_k)
        candidates = []
        for rank, hit in enumerate(hits, 1):
            document_id, version = chunk_identity(hit['chunk'])
            candidates.append(RetrievalCandidate(
                hit['chunk_id'], document_id, version, rank,
                {'bm25_rank': hit['rank'], 'bm25_score': hit['score']}))
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return failed('bm25_search', 'BM25_SEARCH_FAILED', 'BM25 검색 결과를 구성하지 못했습니다.')
    return NodeResult('success', RetrievalCandidates(
        request.request_ref, candidates, {'bm25': index_ref}, request.retrieval_profile),
        {'validate_retrieval_input': StepDiagnostic('success'),
         'bm25_search': StepDiagnostic('success', count=len(candidates))},
        None if candidates else 'no_candidates')


def chunk_identity(chunk):
    """Accept the current demo names at the input boundary, not in the contract."""
    document_id = chunk.get('document_id', chunk.get('doc_id'))
    version = chunk.get('document_version', chunk.get('version_sha256'))
    if (not isinstance(document_id, str) or not document_id.strip()
            or not isinstance(version, str) or not version.strip()):
        raise ValueError('문서 ID·버전이 없는 청크입니다.')
    if (chunk.get('doc_id', document_id) != document_id
            or chunk.get('version_sha256', version) != version):
        raise ValueError('공통 필드와 데모 필드의 문서·버전이 다릅니다.')
    return document_id, version


def search_for_request(request, retriever, top_k):
    allowed = {(d.document_id, d.document_version) for d in request.document_scope}
    if not allowed:
        return []
    # Development baseline: rank the whole valid index, filter, then apply top_k.
    # Filtering only an already-truncated global top_k would miss allowed hits.
    hits = retriever.search(request.search_text, top_k=len(retriever.chunks))
    return [hit for hit in hits if chunk_identity(hit['chunk']) in allowed][:top_k]


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
