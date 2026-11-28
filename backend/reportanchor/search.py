"""N-01 로컬 BM25 검색. 색인은 SQLite 청크 토큰에서 메모리에 다시 만드는 파생 데이터다."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass

from rank_bm25 import BM25Plus

from .store import Store
from .textproc import tokenize, tokenizer_id


@dataclass
class _Index:
    bm25: BM25Plus | None
    chunk_ids: list[str]
    doc_ids: list[str]
    token_sets: list[frozenset[str]]


class SearchIndex:
    CACHE_SIZE = 8

    def __init__(self, store: Store):
        self.store = store
        self._cache: OrderedDict[tuple, _Index] = OrderedDict()
        self._lock = threading.Lock()

    def _build(self, document_ids: list[str]) -> _Index:
        rows = self.store.load_chunks_for_search(document_ids)
        current = tokenizer_id()
        corpus: list[list[str]] = []
        for r in rows:
            toks = json.loads(r["tokens"]) if r["tokenizer"] == current else tokenize(r["text"])
            corpus.append(toks)
        bm25 = BM25Plus(corpus) if corpus and any(corpus) else None
        return _Index(bm25, [r["chunk_id"] for r in rows], [r["document_id"] for r in rows],
                      [frozenset(c) for c in corpus])

    def search(self, versions: dict[str, str], query: str, top_k: int) -> list[dict]:
        """versions: {document_id: document_version}. 문서 버전이 같으면 색인을 재사용한다."""
        key = (tokenizer_id(), tuple(sorted(versions.items())))
        with self._lock:
            idx = self._cache.get(key)
            if idx is not None:
                self._cache.move_to_end(key)
        if idx is None:
            idx = self._build(list(versions))
            with self._lock:
                self._cache[key] = idx
                while len(self._cache) > self.CACHE_SIZE:
                    self._cache.popitem(last=False)
        q = list(dict.fromkeys(tokenize(query)))
        if not q or idx.bm25 is None:
            return []
        scores = idx.bm25.get_scores(q)
        hits = []
        for i, ts in enumerate(idx.token_sets):
            overlap = sum(1 for t in q if t in ts)
            if overlap:  # BM25+는 일치 없는 문서에도 양수 점수를 주므로 일치 토큰이 있는 청크만 후보로 둔다
                hits.append((float(scores[i]), overlap, i))
        # 질의 토큰을 더 많이 포함한 청크를 먼저 두고, 같은 수면 BM25 점수 순으로 정렬한다
        # (작은 개인 문서 집합에서 짧은 청크가 길이 보정만으로 앞서는 것을 막는다).
        hits.sort(key=lambda h: (-h[1], -h[0], h[2]))
        return [
            {"rank": n + 1, "chunk_id": idx.chunk_ids[i], "document_id": idx.doc_ids[i], "score": round(s, 6),
             "matched_terms": ov}
            for n, (s, ov, i) in enumerate(hits[:top_k])
        ]
