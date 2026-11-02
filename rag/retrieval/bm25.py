"""청크를 검색 문서로 사용하는 BM25 기준선.

load_chunks()는 JSONL을 읽고, BM25Retriever는 원본 청크와 인덱스를
함께 보관한다. 인덱스는 객체 생성 시 한 번 만들고 search()에서 재사용한다.
N-01의 공통 NodeResult 반환은 이후 노드 계층에서 연결한다.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path

from rank_bm25 import BM25Okapi

from .tokenizer import TOKENIZER_VERSION, tokenize


def _validate_chunk(chunk: dict, location: str) -> None:
    """검색에 필요한 최소 필드만 확인한다. 전체 원문 계약 검사는 별도다."""
    if not isinstance(chunk, dict):
        raise ValueError(f"{location}: 청크는 JSON 객체여야 합니다.")
    chunk_id = chunk.get("chunk_id")
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        raise ValueError(f"{location}: 비어 있지 않은 chunk_id가 필요합니다.")
    if not isinstance(chunk.get("search_text"), str):
        raise ValueError(f"{location}: search_text는 문자열이어야 합니다.")


def load_chunks(path: str | Path) -> list[dict]:
    """JSONL 한 줄을 청크 하나로 읽는다. 빈 줄은 건너뛴다."""
    path = Path(path).expanduser()
    result = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{path.name}:{line_number}: JSON 형식 오류: {error.msg}"
                ) from error
            _validate_chunk(chunk, f"{path.name}:{line_number}")
            result.append(chunk)
    return result


class BM25Retriever:
    """
    토큰화한 청크로 인덱스를 만들고 질문에 관련된 후보를 반환한다.

    점수는 관련성 순위용이며 정확도나 확률이 아니다.
    검색용 토큰이 없는 청크는 원본과 토큰 목록에서 함께 제외한다.
    """

    def __init__(self, chunks: list[dict]):
        self.chunks: list[dict] = []
        self.skipped_chunk_ids: list[str] = []
        self.tokenizer_version = TOKENIZER_VERSION
        tokenized_corpus: list[list[str]] = []
        seen_ids = set()

        for position, chunk in enumerate(chunks, start=1):
            _validate_chunk(chunk, f"청크 {position}")
            chunk_id = chunk["chunk_id"]
            if chunk_id in seen_ids:
                raise ValueError(f"중복 chunk_id: {chunk_id}")
            seen_ids.add(chunk_id)

            tokens = tokenize(chunk["search_text"])
            if not tokens:
                self.skipped_chunk_ids.append(chunk_id)
                continue

            # 같은 반복문에서 추가하므로 chunks[i]와 tokens[i]의 대응이 유지된다.
            self.chunks.append(deepcopy(chunk))
            tokenized_corpus.append(tokens)

        if not tokenized_corpus:
            raise ValueError("인덱스를 만들 검색 토큰이 없습니다.")

        self.index = BM25Okapi(tokenized_corpus)
        # 이 집합은 단어 겹침 검사 전용이다. BM25에는 중복을 유지한 목록을 전달했다.
        self._token_sets = [set(tokens) for tokens in tokenized_corpus]

    def search(self, question: str, top_k: int = 5) -> list[dict]:
        """질문과 토큰이 겹치는 청크를 점수순으로 반환한다.

        각 결과의 chunk에는 페이지·버전·원문 참조를 포함한 원본 객체가 들어간다.
        질문 토큰이 없거나 모든 청크와 토큰이 겹치지 않으면 빈 목록을 반환한다.
        """
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k는 1 이상의 정수여야 합니다.")

        query_tokens = tokenize(question)
        if not query_tokens:
            return []

        scores = self.index.get_scores(query_tokens)
        query_terms = set(query_tokens)
        candidate_indices = [
            i for i, terms in enumerate(self._token_sets) if query_terms & terms
        ]

        # 점수 동률이면 입력 순서를 유지해 결과를 재현하기 쉽게 한다.
        # 점수 > 0으로 거르지 않는다. 실제 겹치는 토큰이 있어도 0점일 수 있다.
        ranked_indices = sorted(
            candidate_indices, key=lambda i: (-float(scores[i]), i)
        )[:top_k]

        results = []
        for rank, index in enumerate(ranked_indices, start=1):
            chunk = self.chunks[index]
            results.append({
                "chunk_id": chunk["chunk_id"],
                "rank": rank,
                "score": float(scores[index]),
                "matched_tokens": sorted(query_terms & self._token_sets[index]),
                "chunk": deepcopy(chunk),
            })
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description="청크 JSONL에서 BM25 검색")
    parser.add_argument(
        "--chunks", type=Path, required=True,
        help="검색할 chunks.jsonl 파일 경로 (상대 경로는 현재 작업 디렉터리 기준)",
    )
    parser.add_argument("--query", default="2025년 출연금 예산")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    # 사용자별 데이터 위치를 인자로 받으며 특정 프로젝트 경로에 의존하지 않는다.
    path = args.chunks.expanduser().resolve()

    try:
        chunks = load_chunks(path)
        retriever = BM25Retriever(chunks)
        hits = retriever.search(args.query, top_k=args.top_k)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, f"오류: {error}\n")

    print(f"읽은 청크: {len(chunks)}개")
    print(f"인덱스 청크: {retriever.index.corpus_size}개")
    print(f"제외한 빈 토큰 청크: {len(retriever.skipped_chunk_ids)}개")
    print(f"청크당 평균 토큰 수: {retriever.index.avgdl:.1f}")
    print(f"토큰화 정책: {retriever.tokenizer_version}")
    print(f"질문: {args.query}")
    print(f"질문 토큰: {tokenize(args.query)}")

    if not hits:
        print("질문과 토큰이 겹치는 검색 후보가 없습니다. 원문 정보의 부재를 뜻하지는 않습니다.")
    for hit in hits:
        chunk = hit["chunk"]
        print(f"\n[{hit['rank']}] 점수: {hit['score']:.4f}")
        print(f"청크 ID: {hit['chunk_id']}")
        print(f"PDF 페이지: {chunk.get('pdf_pages', [])}")
        print(f"일치 토큰: {hit['matched_tokens']}")
        print(f"원문 미리보기: {chunk.get('text', chunk['search_text'])[:400]}")


if __name__ == "__main__":
    main()
