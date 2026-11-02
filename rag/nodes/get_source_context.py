"""
[N-02]
N-01 결과에는 "Chunk ID / 문서 ID와 버전 / 순위 / 점수"가 들어있ㅇㅁ
-> 답변 생성을 위해서는 실제 문장이 필요함

Step 01: 청크 ID를 받아 원문을 찾아 출력하는 기능
-> N-01 결과의 Chunk_id를 통해 원본 청크에서 찾아, 원문 text + page + 원문 위치 참조

"""

import json
import argparse
from pathlib import Path

from rag.contracts import RetrievalCandidate
from rag.retrieval.bm25 import load_chunks

# 청크를 ID로 찾을 수 있는 딕셔너리로 바꾸기
def build_chunk_lookup(chunks: list[dict])->dict[str, dict]:
    lookup = {}

    for chunk in chunks:
        chunk_id = chunk["chunk_id"]

        if chunk_id in lookup:
            raise ValueError(f"중복된 chunk_id: {chunk_id} 입니다.")

        lookup[chunk_id] = chunk

    return lookup

def lookup_chunk(chunk_id: str, chunk_lookup: dict[str, dict]) -> dict:
    """청크 ID로 저장된 원문 청크 하나를 찾는다."""
    
    chunk = chunk_lookup.get(chunk_id)
    if chunk is None:
        raise ValueError(f"존재하지 않는 청크 ID입니다. {chunk_id}")

    return chunk

def get_source_for_candidate(candidate: RetrievalCandidate, chunk_lookup: dict[str, dict]) -> dict:
    source_chunk = lookup_chunk(chunk_id=candidate.chunk_id, chunk_lookup=chunk_lookup)
    
    if source_chunk["doc_id"] != candidate.document_id:
        raise ValueError("검색 후보와 원문의 문서 ID가 다릅니다.")

    if source_chunk["version_sha256"] != candidate.document_version:
        raise ValueError("검색 후보와 원문의 문서 버전이 다릅니다.")

    return source_chunk


def load_block_lookup(path: Path) -> dict[str, dict]:
    """
    블록 조회표 구성
    """
    lookup = {}

    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue

            block = json.loads(line)
            block_id = block["block_id"]

            if block_id in lookup:
                raise ValueError(f"중복된 block_id입니다: {block_id}")

            lookup[block_id] = block

    return lookup

def reconstruct_chunk_text(source_chunk: dict, block_lookup: dict[str, dict]) -> str:
    """원본 블록의 문자 구간들을 연결해 청크 내용을 복원한다."""
    fragments = []

    if not source_chunk["source_spans"]:
        raise ValueError("청크에 원문 구간 참조가 없습니다.")

    for span in source_chunk["source_spans"]:
        block = block_lookup.get(span["block_id"])

        if block is None:
            raise ValueError(
                f"원본 블록을 찾지 못했습니다: {span['block_id']}"
            )

        if block["doc_id"] != source_chunk["doc_id"]:
            raise ValueError("청크와 원본 블록의 문서 ID가 다릅니다.")

        if block["version_sha256"] != source_chunk["version_sha256"]:
            raise ValueError("청크와 원본 블록의 문서 버전이 다릅니다.")

        if block["pdf_page"] != span["pdf_page"]:
            raise ValueError("원문 구간과 블록의 페이지가 다릅니다.")

        start = span["start_char"]
        end = span["end_char"]
        text = block["text"]

        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(text)
        ):
            raise ValueError("원문 문자 구간이 올바르지 않습니다.")

        fragments.append(text[start:end])

    return "\n".join(fragments)

def group_blocks_by_page(block_lookup: dict[str, dict]) -> dict[tuple[str, str, int], list[dict]]:
    page_blocks = {}

    for block in block_lookup.values():
        key = (
            block["doc_id"],
            block["version_sha256"],
            block["pdf_page"],
        )

        if key not in page_blocks:
            page_blocks[key] = []

        page_blocks[key].append(block)

    return page_blocks

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--retrieval-result", type=Path, required=True)
    parser.add_argument("--blocks", type=Path, required=True)

    args = parser.parse_args()

    # 1. 원문 청크를 읽고 조회표 준비
    path = args.chunks.expanduser().resolve()
    chunks = load_chunks(path)
    chunk_lookup = build_chunk_lookup(chunks)

    block_path = args.blocks.expanduser().resolve()
    block_lookup = load_block_lookup(block_path)
    page_blocks = group_blocks_by_page(block_lookup)

    # 2. N-01이 출력한 JSON 읽기
    result_path = args.retrieval_result.expanduser().resolve()
    payload = json.loads(
        result_path.read_text(encoding="utf-8")
    )

    # 이번 개발 단계에서는 정상 완료된 결과만 처리
    if payload["status"] != "success" or payload["data"] is None:
        print("정상 완료된 검색 결과가 없어 원문 조회를 진행하지 않습니다.")
        return

    # 3. 검색 후보를 하나씩 원문과 연결
    for item in payload["data"]["candidates"]:
        candidate = RetrievalCandidate(**item)

        source_chunk = get_source_for_candidate(
            candidate=candidate,
            chunk_lookup=chunk_lookup,
        )

        reconstructed_text = reconstruct_chunk_text(source_chunk=source_chunk, block_lookup=block_lookup)

        if reconstructed_text != source_chunk["text"]:
            raise ValueError(f"원본 블록과 청크의 내용이 일치하지 않습니다. {candidate.chunk_id}")

        page_key = (
            candidate.document_id,
            candidate.document_version,
            source_chunk["pdf_pages"][0],
        )

        blocks_on_page = page_blocks.get(page_key)

        if blocks_on_page is None:
            raise ValueError("해당 문서·버전·페이지의 블록을 찾지 못했습니다.")

        print("같은 페이지의 전체 블록 수:", len(blocks_on_page))

        print("원문 복원 일치: ", True)
        print("사용할 원문 구간 수: ", len(source_chunk["source_spans"]))

        """
        first_span = source_chunk["source_spans"][0]
        block_id = first_span["block_id"]

        source_block = block_lookup.get(block_id)

        if source_block is None:
            raise ValueError(f"원본 블록을 찾지 못했습니다: {block_id}")

        print("첫 원본 블록 ID:", block_id)
        print("첫 원본 블록 내용:", source_block["text"])
        """

        # 4. 문서·버전 검사에 통과한 원문 확인
        print("검색 순위:", candidate.rank)
        print("청크 ID:", candidate.chunk_id)
        print("PDF 페이지:", source_chunk["pdf_pages"])
        print("원문 미리보기:")
        print(source_chunk["text"][:300])


if __name__ == "__main__":
    main()