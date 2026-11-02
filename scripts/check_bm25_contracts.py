"""
실제 청크 → BM25 → 계약 객체 → JSON을 확인하는 개발용 점검.

N-01 정식 구현이나 검색 정확도 벤치마크가 아니다. doc_id는 이 데모 안에서만
document_id에 대응시키며, 등록 계층의 논리 문서 ID 계약을 확정하지 않는다.

* 다시 말해, bm25.py와 contracts.py를 연결하는 기능 임시 점검 파일이다.
실제 서비스에서 사용할 N-01은 retireve_evidence.py 이다.
"""
import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path

from rag.contracts import (DocumentRef, NodeResult, RetrievalCandidate,
                           RetrievalCandidates, RetrievalRequest, StepDiagnostic)
from rag.retrieval import tokenizer
from rag.retrieval.bm25 import BM25Retriever, load_chunks


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--query", action="append", help="검색 질문. 여러 번 지정할 수 있음")
    args = parser.parse_args()
    path = args.chunks.expanduser().resolve()
    output = args.out_dir.expanduser().resolve()
    chunks = load_chunks(path)
    input_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    retriever = BM25Retriever(chunks)
    scope = sorted({(c["doc_id"], c["version_sha256"]) for c in chunks})
    references = [DocumentRef(doc, revision) for doc, revision in scope]

    # 저장한 인덱스 파일이 아닌, 이번 메모리 인덱스의 입력·설정 구성을 식별한다.
    manifest = {
        "index_kind": "in_memory_bm25",
        "chunks_sha256": input_hash,
        "tokenizer_sha256": hashlib.sha256(Path(tokenizer.__file__).read_bytes()).hexdigest(),
        "tokenizer_version": tokenizer.TOKENIZER_VERSION,
        "kiwipiepy_version": version("kiwipiepy"),
        "rank_bm25_version": version("rank-bm25"),
        "parameters": {"k1": retriever.index.k1, "b": retriever.index.b,
                       "epsilon": retriever.index.epsilon},
        "indexed_chunk_ids": [c["chunk_id"] for c in retriever.chunks],
    }
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    index_ref = "bm25-memory:" + fingerprint
    queries = args.query or ["조달재원 출연금", "범부처 연구과제관리서비스 표준화",
                            "2025년 출연금 예산", "zxqvunseen987654321"]
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "index_manifest.json", {"index_ref": index_ref, **manifest})
    reports = []
    by_id = {c["chunk_id"]: c for c in chunks}
    for number, question in enumerate(queries, start=1):
        request = RetrievalRequest(f"smoke_{number:03}", question, references,
                                   "bm25-only-development-v1")
        raw_hits = retriever.search(request.search_text, args.top_k)
        candidates = []
        for hit in raw_hits:
            source = by_id[hit["chunk_id"]]
            if source != hit["chunk"]:
                raise ValueError("검색 결과의 원본 청크 대응이 어긋났습니다.")
            if (source["doc_id"], source["version_sha256"]) not in scope:
                raise ValueError("허용 범위를 벗어난 후보입니다.")
            candidates.append(RetrievalCandidate(
                chunk_id=hit["chunk_id"], document_id=source["doc_id"],
                document_version=source["version_sha256"], rank=hit["rank"],
                score_metadata={"bm25_rank": hit["rank"], "bm25_score": hit["score"]},
            ))
        result = NodeResult[RetrievalCandidates](
            status="success",
            data=RetrievalCandidates(request.request_ref, candidates,
                                     {"bm25": index_ref}, "bm25-only-development-v1"),
            diagnostics={"bm25_search": StepDiagnostic("success", count=len(candidates))},
            # 하이브리드 N-01 결과 코드 정책을 대신 결정하지 않는다.
            outcome=None,
        )
        payload = asdict(result)
        if set(payload) != {"status", "data", "diagnostics", "outcome"}:
            raise ValueError("공통 반환 필드가 일치하지 않습니다.")
        write_json(output / f"result_{number:03}.json", payload)
        reports.append({
            "question": question,
            "result_file": f"result_{number:03}.json",
            "candidate_count": len(candidates),
            "hits": [{"rank": hit["rank"], "score": hit["score"],
                      "chunk_id": hit["chunk_id"], "pdf_pages": hit["chunk"]["pdf_pages"],
                      "matched_tokens": hit["matched_tokens"],
                      "text_preview": hit["chunk"].get("text", "")[:240]} for hit in raw_hits],
        })

    summary = {
        "test_scope": "BM25 + contract serialization smoke test, not full N-01 or answer generation",
        "source_chunks": str(path), "chunks_sha256": input_hash,
        "source_unchanged": input_hash == hashlib.sha256(path.read_bytes()).hexdigest(),
        "loaded_chunks": len(chunks), "indexed_chunks": retriever.index.corpus_size,
        "index_ref": index_ref, "scope_mapping": "demo doc_id alias; not a registered logical ID",
        "provenance_checks": "passed", "queries": reports,
        "limitations": ["Dense/RRF/reranker and N-02/N-04/N-03 not run.",
                        "No generated answers, precision/recall metrics, or exhaustive relevance gold set."],
    }
    if not summary["source_unchanged"]:
        raise ValueError("실행 중 입력 파일이 변경됐습니다.")
    write_json(output / "summary.json", summary)
    print(json.dumps({"loaded_chunks": len(chunks), "indexed_chunks": retriever.index.corpus_size,
                      "queries": len(queries), "provenance_checks": "passed",
                      "output_dir": str(output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
