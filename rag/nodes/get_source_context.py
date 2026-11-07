"""
N-02: 검색 후보의 원문을 조회하고 고정 길이 문맥을 EvidenceBundle로 반환한다.

검색·생성·사실 검증을 하지 않는다. 원문 저장 순서를 유지하며, 같은 문서/버전/
페이지에서 청크별 문맥을 독립적으로 보존한다. 후보 원문은 예산 때문에 일부만 잘라내지 않는다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping

from rag.contracts import (
    ContextExpansionConfig, DocumentRef, EvidenceBundle, EvidenceItem,
    NodeResult, RetrievalCandidate, RetrievalCandidates, SourceSpan, StepDiagnostic, FailedItem,
)


class SourceContextError(ValueError):
    def __init__(self, code: str, reason: str, *, fatal: bool = False):
        super().__init__(reason)
        self.code = code
        self.fatal = fatal


def build_chunk_lookup(chunks: list[dict]) -> dict[str, dict]:
    lookup = {}
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise ValueError("유효한 chunk_id가 필요합니다.")
        if chunk_id in lookup:
            raise ValueError(f"중복된 chunk_id: {chunk_id}")
        lookup[chunk_id] = chunk
    return lookup


def lookup_chunk(chunk_id: str, chunk_lookup: Mapping[str, dict]) -> dict:
    chunk = chunk_lookup.get(chunk_id)
    if chunk is None:
        raise SourceContextError("CHUNK_NOT_FOUND", "검색 후보의 원문 청크가 없습니다.")
    return chunk


def get_source_for_candidate(candidate: RetrievalCandidate, chunk_lookup: Mapping[str, dict]) -> dict:
    chunk = lookup_chunk(candidate.chunk_id, chunk_lookup)
    if chunk.get("chunk_id") != candidate.chunk_id:
        raise SourceContextError("CHUNK_ID_MISMATCH", "청크 조회표의 ID가 일치하지 않습니다.", fatal=True)
    if (chunk.get("doc_id"), chunk.get("version_sha256")) != (
            candidate.document_id, candidate.document_version):
        raise SourceContextError("DOCUMENT_VERSION_MISMATCH", "검색 후보와 원문의 문서·버전이 다릅니다.", fatal=True)
    return chunk


def load_block_lookup(path: Path) -> dict[str, dict]:
    lookup = {}
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            block = json.loads(line)
            if not isinstance(block, dict):
                raise ValueError("블록 행은 JSON 객체여야 합니다.")
            block_id = block.get("block_id")
            if not isinstance(block_id, str) or not block_id.strip():
                raise ValueError("유효한 block_id가 필요합니다.")
            if block_id in lookup:
                raise ValueError(f"중복된 block_id: {block_id}")
            lookup[block_id] = block
    return lookup


def group_blocks_by_page(block_lookup: Mapping[str, dict]) -> dict[tuple[str, str, int], list[dict]]:
    """block_index로 재정렬하지 않고 JSONL에서 읽은 순서를 유지한다."""
    pages = {}
    for key, block in block_lookup.items():
        if key != block.get("block_id"):
            raise SourceContextError("BLOCK_ID_MISMATCH", "블록 조회표의 ID가 일치하지 않습니다.", fatal=True)
        identity = (block["doc_id"], block["version_sha256"], block["pdf_page"])
        if type(identity[2]) is not int or identity[2] < 1:
            raise SourceContextError("INVALID_PAGE", "원문 페이지가 올바르지 않습니다.", fatal=True)
        pages.setdefault(identity, []).append(block)
    return pages


def _locator(block: dict, page: int) -> dict:
    locator = block.get("source_locator")
    if (not isinstance(locator, dict) or locator.get("type") != "pdf_page_bbox"
            or type(locator.get("pdf_page")) is not int or locator["pdf_page"] != page
            or locator.get("units") != "pt"
            or locator.get("coordinate_space") != "pymupdf_unrotated_page"):
        raise SourceContextError("INVALID_SOURCE_LOCATOR", "원문 위치 참조가 올바르지 않습니다.", fatal=True)
    bbox = locator.get("bbox")
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in bbox)
            or not bbox[0] < bbox[2] or not bbox[1] < bbox[3]):
        raise SourceContextError("INVALID_SOURCE_LOCATOR", "원문 좌표가 올바르지 않습니다.", fatal=True)
    return deepcopy(locator)


def _source_span(block: dict, start: int, end: int) -> SourceSpan:
    text = block.get("text")
    if (not isinstance(text, str) or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(text)):
        raise SourceContextError("INVALID_SOURCE_SPAN", "원문 문자 구간이 올바르지 않습니다.", fatal=True)
    page = block["pdf_page"]
    return SourceSpan(block["block_id"], page, start, end, text[start:end], _locator(block, page),
                      block.get("extraction_method", "unknown"), block.get("verification_status", "unverified"))


def _anchor_spans(chunk: dict, blocks: Mapping[str, dict]) -> list[SourceSpan]:
    spans = chunk.get("source_spans")
    if not isinstance(spans, list) or not spans:
        raise SourceContextError("INVALID_SOURCE_SPAN", "청크에 원문 구간 참조가 없습니다.", fatal=True)
    result = []
    for span in spans:
        block = blocks.get(span["block_id"])
        if block is None:
            raise SourceContextError("BLOCK_NOT_FOUND", "참조된 원본 블록이 없습니다.")
        if (block.get("doc_id"), block.get("version_sha256")) != (
                chunk.get("doc_id"), chunk.get("version_sha256")):
            raise SourceContextError("DOCUMENT_VERSION_MISMATCH", "청크와 원본 블록의 문서·버전이 다릅니다.", fatal=True)
        if type(span.get("pdf_page")) is not int or block["pdf_page"] != span["pdf_page"]:
            raise SourceContextError("SOURCE_PAGE_MISMATCH", "청크와 원본 블록의 페이지가 다릅니다.", fatal=True)
        canonical = _source_span(block, span["start_char"], span["end_char"])
        if span.get("source_locator") != canonical.source_locator:
            raise SourceContextError("SOURCE_LOCATOR_MISMATCH", "청크와 원본 블록의 위치 참조가 다릅니다.", fatal=True)
        result.append(canonical)
    pages = chunk.get("pdf_pages")
    if (not isinstance(pages, list) or any(type(p) is not int for p in pages)
            or pages != sorted(set(s.pdf_page for s in result))):
        raise SourceContextError("SOURCE_PAGE_MISMATCH", "청크의 페이지 목록과 원문 구간이 다릅니다.", fatal=True)
    return result


def reconstruct_chunk_text(source_chunk: dict, block_lookup: Mapping[str, dict]) -> str:
    return "\n".join(span.text for span in _anchor_spans(source_chunk, block_lookup))


@dataclass
class _Page:
    key: tuple[str, str, int]
    blocks: list[dict]
    text: str
    offsets: dict[str, tuple[int, int]]

    @classmethod
    def build(cls, key, blocks):
        kept, parts, offsets, cursor = [], [], {}, 0
        for block in blocks:
            text = block.get("text")
            if not isinstance(text, str):
                raise SourceContextError("INVALID_BLOCK", "원본 블록 텍스트가 올바르지 않습니다.", fatal=True)
            if not text:
                continue
            _locator(block, key[2])
            if parts:
                cursor += 1  # 블록 사이 줄바꿈
            offsets[block["block_id"]] = (cursor, cursor + len(text))
            kept.append(block)
            parts.append(text)
            cursor += len(text)
        return cls(key, kept, "\n".join(parts), offsets)

    def spans(self, start: int, end: int) -> list[SourceSpan]:
        result = []
        for block in self.blocks:
            left, right = self.offsets[block["block_id"]]
            a, b = max(start, left), min(end, right)
            if a < b:
                result.append(_source_span(block, a - left, b - left))
        return result











def _failed(code: str, reason: str, diagnostics=None):
    return NodeResult(status="error", data=None, diagnostics={
        **(diagnostics or {}),
        "get_source_context": StepDiagnostic("failed", code, reason, False),
    }, outcome=None)


def get_source_context(
    retrieval: RetrievalCandidates,
    chunk_lookup: Mapping[str, dict],
    block_lookup: Mapping[str, dict],
    *,
    document_scope: list[DocumentRef],
    config: ContextExpansionConfig | None = None,
    context_profile_ref: str | None = None,
) -> NodeResult[EvidenceBundle]:
    """One retrieval chunk produces one evidence item; overlapping items stay separate."""
    diagnostics = {}
    try:
        config = ContextExpansionConfig() if config is None else config
        if not isinstance(config, ContextExpansionConfig):
            raise ValueError('잘못된 문맥 설정입니다.')
        config.__post_init__()
        if (not isinstance(retrieval, RetrievalCandidates)
                or not isinstance(retrieval.request_ref, str) or not retrieval.request_ref.strip()
                or not isinstance(retrieval.candidates, list)):
            raise ValueError('검색 후보 형식이 올바르지 않습니다.')
        if not isinstance(chunk_lookup, Mapping) or not isinstance(block_lookup, Mapping):
            raise ValueError('청크·블록 조회표가 필요합니다.')
        if not isinstance(document_scope, list) or any(not isinstance(d, DocumentRef) for d in document_scope):
            raise ValueError('문서 범위 형식이 올바르지 않습니다.')
        for document in document_scope:
            document.__post_init__()
        allowed = {(d.document_id, d.document_version) for d in document_scope}
        identities = {}
        for candidate in retrieval.candidates:
            if not isinstance(candidate, RetrievalCandidate):
                raise ValueError('검색 후보 형식이 올바르지 않습니다.')
            candidate.__post_init__()
            identity = candidate.document_id, candidate.document_version
            if identity not in allowed:
                return _failed('DOCUMENT_SCOPE_MISMATCH', '허용되지 않은 문서·버전의 후보입니다.')
            if candidate.chunk_id in identities and identities[candidate.chunk_id] != identity:
                return _failed('CONFLICTING_CANDIDATE_REFERENCE', '같은 청크 ID가 서로 다른 문서·버전을 가리킵니다.')
            identities[candidate.chunk_id] = identity
        if context_profile_ref is None:
            digest = hashlib.sha256(json.dumps(asdict(config), sort_keys=True).encode()).hexdigest()[:16]
            context_profile_ref = 'fixed-page-window-v2:' + digest
        if not isinstance(context_profile_ref, str) or not context_profile_ref.strip():
            raise ValueError('문맥 설정 참조가 필요합니다.')
        if not retrieval.candidates:
            return NodeResult('success', EvidenceBundle(retrieval.request_ref, [], context_profile_ref), {}, None)
        grouped = group_blocks_by_page(block_lookup)
    except SourceContextError as error:
        return _failed(error.code, str(error))
    except (ValueError, TypeError, KeyError, AttributeError):
        return _failed('INVALID_SOURCE_CONTEXT_INPUT', '입력·설정·원문 조회표를 확인하세요.')

    diagnostics['validate_source_context_input'] = StepDiagnostic('success')
    items, failed_items, excluded, seen, pages = [], [], [], set(), {}
    used_chars, duplicates, budget_closed = 0, 0, False
    for candidate in sorted(retrieval.candidates, key=lambda c: c.rank):
        if candidate.chunk_id in seen:
            duplicates += 1
            continue
        seen.add(candidate.chunk_id)
        try:
            chunk = get_source_for_candidate(candidate, chunk_lookup)
            spans = _anchor_spans(chunk, block_lookup)
            if '\n'.join(s.text for s in spans) != chunk.get('text'):
                raise SourceContextError('RECONSTRUCTION_MISMATCH', '청크와 원본 블록 구간이 다릅니다.', fatal=True)
            by_page = {}
            for span in spans:
                by_page.setdefault(span.pdf_page, []).append(span)
            expanded, anchor_parts, truncated = [], [], False
            for page_number, anchors in by_page.items():
                key = candidate.document_id, candidate.document_version, page_number
                if key not in pages:
                    pages[key] = _Page.build(key, grouped.get(key, []))
                page = pages[key]
                start = page.offsets[anchors[0].block_id][0] + anchors[0].start_char
                end = page.offsets[anchors[-1].block_id][0] + anchors[-1].end_char
                anchor = '\n'.join(s.text for s in anchors)
                if page.text[start:end] != anchor:
                    raise SourceContextError('NONCONTIGUOUS_SOURCE_SPANS', '원문 구간이 연속적이지 않습니다.', fatal=True)
                left, right = max(0, start - config.before_chars), min(len(page.text), end + config.after_chars)
                expanded.append('\n'.join(s.text for s in page.spans(left, right)))
                anchor_parts.append(anchor)
                truncated |= left != start - config.before_chars or right != end + config.after_chars
            context_text = '\n\n'.join(expanded)
            if used_chars + len(context_text) > config.max_total_chars:
                context_text = '\n\n'.join(anchor_parts)
                truncated = True
            if budget_closed or used_chars + len(context_text) > config.max_total_chars:
                budget_closed = True
                excluded.append(candidate.chunk_id)
                continue
            source_refs = [dict(block_id=s.block_id, start_offset=s.start_char,
                                end_offset=s.end_char, source_locator=deepcopy(s.source_locator),
                                extraction_method=s.extraction_method, verification_status=s.verification_status) for s in spans]
            items.append(EvidenceItem(candidate.chunk_id, candidate.document_id,
                candidate.document_version, candidate.rank, context_text, source_refs, truncated))
            used_chars += len(context_text)
        except SourceContextError as error:
            if error.fatal:
                return _failed(error.code, str(error), diagnostics)
            failed_items.append(FailedItem(candidate.chunk_id, error.code, False))
        except (KeyError, TypeError, ValueError, AttributeError):
            return _failed('INVALID_SOURCE_DATA', '원문 데이터의 형식·위치·순서를 확인하세요.', diagnostics)
    diagnostics['resolve_sources'] = StepDiagnostic(
        'failed' if failed_items else 'success',
        error_code='SOURCES_UNAVAILABLE' if failed_items else None,
        reason='일부 원문을 조회하지 못했습니다.' if failed_items else None,
        count=len(items), failed_items=failed_items)
    diagnostics['deduplicate_candidates'] = StepDiagnostic('success', count=duplicates)
    diagnostics['context_budget'] = StepDiagnostic(
        'skipped' if excluded else 'success', count=len(items), excluded_refs=excluded,
        reason='출력 상한으로 낮은 순위 후보를 제외했습니다.' if excluded else None)
    if failed_items and not items:
        return _failed('NO_USABLE_SOURCE_CONTEXT', '반환할 원문 근거를 확보하지 못했습니다.', diagnostics)
    return NodeResult('partial' if failed_items else 'success',
        EvidenceBundle(retrieval.request_ref, items, context_profile_ref), diagnostics, None)


def load_retrieval_result(path: Path) -> RetrievalCandidates:
    """CLI 어댑터: N-01의 success/partial 응답에서 업무 데이터만 읽는다."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("status") not in {"success", "partial"}:
        raise SourceContextError("UPSTREAM_RETRIEVAL_FAILED", "사용 가능한 N-01 검색 결과가 없습니다.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("검색 후보 데이터가 필요합니다.")
    return RetrievalCandidates(data["request_ref"], [RetrievalCandidate(**c) for c in data["candidates"]],
                               data["index_refs"], data["strategy_version"])


def main() -> int:
    # 함수형 N-02를 사용할 때에는 BM25·Kiwi를 불러올 필요가 없다.
    from rag.retrieval.bm25 import load_chunks
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--blocks", type=Path, required=True)
    parser.add_argument("--retrieval-result", type=Path)
    parser.add_argument("--chunk-id", help="검색 결과 파일 없이 한 청크로 확인")
    parser.add_argument("--before-chars", type=int, default=200)
    parser.add_argument("--after-chars", type=int, default=200)
    parser.add_argument("--max-total-chars", type=int, default=12000)
    parser.add_argument("--scope", nargs=2, action="append", metavar=("DOCUMENT_ID", "VERSION"),
                        help="허용 문서·버전 쌍. 반복 지정 가능. 생략하면 로컬 청크 파일의 범위 사용")
    args = parser.parse_args()
    try:
        config = ContextExpansionConfig(args.before_chars, args.after_chars, args.max_total_chars)
        chunks = load_chunks(args.chunks.expanduser().resolve())
        lookup = build_chunk_lookup(chunks)
        blocks = load_block_lookup(args.blocks.expanduser().resolve())
        if bool(args.retrieval_result) == bool(args.chunk_id):
            raise ValueError("--retrieval-result와 --chunk-id 중 하나만 지정해야 합니다.")
        if args.chunk_id:
            chunk = lookup_chunk(args.chunk_id, lookup)
            retrieval = RetrievalCandidates("cli_source_context", [RetrievalCandidate(
                args.chunk_id, chunk["doc_id"], chunk["version_sha256"], 1, {})], {}, "source-context-demo")
        else:
            retrieval = load_retrieval_result(args.retrieval_result.expanduser().resolve())
        pairs = args.scope if args.scope is not None else sorted({(c["doc_id"], c["version_sha256"]) for c in chunks})
        scope = [DocumentRef(*pair) for pair in pairs]
        result = get_source_context(retrieval, lookup, blocks, document_scope=scope, config=config)
    except SourceContextError as error:
        result = _failed(error.code, str(error))
    except (ValueError, TypeError, KeyError, OSError):
        result = _failed("INVALID_SOURCE_CONTEXT_INPUT", "입력 파일·설정·참조 형식을 확인해야 합니다.")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, allow_nan=False))
    return 2 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
