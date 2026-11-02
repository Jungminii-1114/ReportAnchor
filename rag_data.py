"""Load extracted document JSON and build provenance-preserving text chunks.

Standard library only: this module does not parse PDFs, call a VLM, build a
search index, or decide whether an extracted claim is true.

rag_data verify an extracted document data.
It is kind of preprocessing module that makes chunk for search.

NW의 Parser/VLM 출력이 준비되지 않은 상황에서, 자체적으로 만든 PyMuPDF 데모 결과이다.
-> RAG 개발을 위한 데모 파일.

pdf_extract.py  :  PDF에서 텍스트/블록/페이지 정보를 추출하는 임시 Parser
rag_data.py     :  추출된 데이터를 검증하고, RAG용 청크로 묶는 코드 

"""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
CHUNK_SCHEMA = "rag-chunk/1"
CHUNKER_VERSION = "page-block-packer/1"


class DataValidationError(ValueError):
    """An input is missing, malformed, or points to another document/version."""


@dataclass(frozen=True)
class ChunkingConfig:
    max_chars: int = 1000
    overlap_chars: int = 120
    break_on_headings: bool = True

    def __post_init__(self):
        if type(self.max_chars) is not int or self.max_chars < 32:
            raise ValueError("max_chars는 32 이상의 정수여야 합니다.")
        if type(self.overlap_chars) is not int or not 0 <= self.overlap_chars < self.max_chars:
            raise ValueError("overlap_chars는 0 이상 max_chars 미만이어야 합니다.")
        if type(self.break_on_headings) is not bool:
            raise ValueError("break_on_headings는 boolean이어야 합니다.")

    @property
    def config_id(self):
        return digest({"chunker": CHUNKER_VERSION, **asdict(self)})[:12]


@dataclass(frozen=True)
class ParsedDocument:
    manifest: dict[str, Any]
    pages: tuple[dict[str, Any], ...]
    blocks: tuple[dict[str, Any], ...]
    input_dir: Path
    input_hashes: dict[str, str]
    extraction_fingerprint: str


@dataclass(frozen=True)
class _Unit:
    block: dict[str, Any]
    start: int
    end: int
    notes: tuple[str, ...] = ()

    @property
    def text(self):
        return self.block["text"][self.start:self.end]


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read(path, jsonl=False):
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if jsonl:
            records = []
            for number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DataValidationError(f"{path.name}:{number}: JSON 형식 오류") from exc
                if not isinstance(record, dict):
                    raise DataValidationError(f"{path.name}:{number}: 객체가 필요합니다.")
                records.append(record)
            return records, hashlib.sha256(raw).hexdigest()
        value = json.loads(text)
        if not isinstance(value, dict):
            raise DataValidationError(f"{path.name}: 객체가 필요합니다.")
        return value, hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataValidationError(f"{path}: 읽기/JSON 오류: {exc}") from exc


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def load_document(input_dir: str | Path) -> ParsedDocument:
    """Validate one manifest + pages + blocks set without reopening its PDF.

    Page order is normalized. Within a page, JSONL order is preserved because
    PyMuPDF's block_index is an identity, not necessarily its reading order.
    """
    root = Path(input_dir).expanduser().resolve()
    manifest, mh = _read(root / "manifest.json")
    pages, ph = _read(root / "pages.jsonl", jsonl=True)
    blocks, bh = _read(root / "blocks.jsonl", jsonl=True)
    doc_id = manifest.get("doc_id")
    version = manifest.get("version_sha256")
    count = manifest.get("page_count")
    if not _nonempty(doc_id) or not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{64}", version):
        raise DataValidationError("manifest.json: doc_id 또는 SHA-256 형식이 올바르지 않습니다.")
    if type(count) is not int or count <= 0:
        raise DataValidationError("manifest.json: page_count는 양의 정수여야 합니다.")
    if not isinstance(manifest.get("metadata", {}), dict):
        raise DataValidationError("manifest.json: metadata는 객체여야 합니다.")

    def identity(row, label):
        if row.get("doc_id") != doc_id or row.get("version_sha256") != version:
            raise DataValidationError(f"{label}: 문서 ID 또는 버전 불일치")
        page = row.get("pdf_page")
        if type(page) is not int or not 1 <= page <= count:
            raise DataValidationError(f"{label}: PDF 페이지가 범위를 벗어났습니다.")
        if not isinstance(row.get("text"), str):
            raise DataValidationError(f"{label}: text는 문자열이어야 합니다.")
        return page

    seen_pages = set()
    for index, page in enumerate(pages, 1):
        n = identity(page, f"pages.jsonl 레코드 {index}")
        if n in seen_pages:
            raise DataValidationError(f"pages.jsonl: 중복 페이지 {n}")
        seen_pages.add(n)
        if type(page.get("char_count")) is not int or page["char_count"] != len(page["text"].strip()):
            raise DataValidationError(f"pages.jsonl: 페이지 {n}의 char_count 불일치")
    if seen_pages != set(range(1, count + 1)):
        raise DataValidationError("pages.jsonl: manifest의 전체 페이지 기록이 필요합니다. 빈 페이지도 포함하세요.")

    seen_blocks = set()
    grouped = defaultdict(list)
    for index, block in enumerate(blocks, 1):
        n = identity(block, f"blocks.jsonl 레코드 {index}")
        bid = block.get("block_id")
        if not _nonempty(bid) or bid in seen_blocks:
            raise DataValidationError(f"blocks.jsonl 레코드 {index}: block_id 누락 또는 중복")
        seen_blocks.add(bid)
        if not block["text"].strip():
            raise DataValidationError(f"blocks.jsonl: 빈 텍스트 블록 {bid}")
        locator = block.get("source_locator")
        if not isinstance(locator, dict):
            raise DataValidationError(f"blocks.jsonl: 출처 위치가 없습니다: {bid}")
        if "pdf_page" in locator and locator["pdf_page"] != n:
            raise DataValidationError(f"blocks.jsonl: 출처 페이지 불일치: {bid}")
        if "bbox" in locator:
            box = locator["bbox"]
            if (not isinstance(box, list) or len(box) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in box) or
                box[0] > box[2] or box[1] > box[3]):
                raise DataValidationError(f"blocks.jsonl: 잘못된 bbox: {bid}")
        grouped[n].append(block)
    for page in pages:
        if bool(page["text"].strip()) != bool(grouped.get(page["pdf_page"])):
            raise DataValidationError(f"페이지 텍스트와 블록의 존재 여부 불일치: {page['pdf_page']}")
    # No timestamp or local pathname in extraction identity. A moved dataset
    # keeps its chunk IDs; changed source records invalidate them.
    fingerprint = digest({"pages_sha256": ph, "blocks_sha256": bh,
                          "parser": manifest.get("metadata", {}).get("parser"),
                          "parser_version": manifest.get("metadata", {}).get("parser_version")})
    return ParsedDocument(manifest, tuple(sorted(pages, key=lambda p: p["pdf_page"])),
                          tuple(b for n in sorted(grouped) for b in grouped[n]), root,
                          {"manifest.json": mh, "pages.jsonl": ph, "blocks.jsonl": bh}, fingerprint)


def _heading(text):
    text = text.strip()
    if len(text) > 100 or text.count("\n") > 1:
        return False
    return bool(re.match(r"^(?:[■□▶]|(?:전략|성과)목표\s*\d+\s*[:：]|제\s*\d+\s*[장절](?:\s|$)|\d+(?:\.\d+)*[.)]\s+\S)", text))


def _units(block, limit):
    text = block["text"]
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        notes = []
        if len(text) > limit:
            notes.append("oversized_block_split")
        if end < len(text):
            # Prefer newline or sentence endings in the latter half. Then a
            # word boundary; only use a hard cut if the text has no boundary.
            window = text[start:end]
            boundaries = [m.end() for m in re.finditer(r"\n|[.!?。！？](?:\s|$)", window) if m.end() >= limit // 2]
            if not boundaries:
                boundaries = [m.end() for m in re.finditer(r"\s+", window) if m.end() >= limit // 2]
            if boundaries:
                end = start + boundaries[-1]
            else:
                notes.append("hard_character_split")
        yield _Unit(block, start, end, tuple(notes))
        start = end


def _length(units):
    return sum(len(u.text) for u in units) + max(0, len(units) - 1)


def _overlap(units, budget):
    selected = []
    for unit in reversed(units):
        if _heading(unit.text):
            break
        if _length([unit, *selected]) > budget:
            break
        selected.insert(0, unit)
    return selected


def build_chunks(document: ParsedDocument, config: ChunkingConfig | None = None) -> list[dict[str, Any]]:
    """Pack adjacent whole blocks within a page; retain exact source offsets.

    max_chars counts Python Unicode characters, not tokens. Overlap repeats
    only a complete short suffix of source blocks, and can therefore be zero.
    It does not cross page or explicit heading boundaries.
    """
    config = config or ChunkingConfig()
    manifest = document.manifest
    grouped = defaultdict(list)
    for block in document.blocks:
        grouped[block["pdf_page"]].extend(_units(block, config.max_chars))
    chunks = []

    def emit(units):
        text = "\n".join(u.text for u in units)
        spans = [{"block_id": u.block["block_id"], "pdf_page": u.block["pdf_page"],
                  "start_char": u.start, "end_char": u.end,
                  "source_locator": copy.deepcopy(u.block["source_locator"])} for u in units]
        identity = {"doc_id": manifest["doc_id"], "version_sha256": manifest["version_sha256"],
                    "extraction_fingerprint": document.extraction_fingerprint,
                    "chunker_version": CHUNKER_VERSION, "config": asdict(config),
                    "source_spans": spans, "text": text}
        chunks.append({"schema_version": CHUNK_SCHEMA,
                       "chunk_id": manifest["doc_id"] + ":c_" + digest(identity)[:20],
                       "doc_id": manifest["doc_id"], "version_sha256": manifest["version_sha256"],
                       "document_title": manifest.get("document_title"),
                       "title_verified_by_human": manifest.get("title_verified_by_human", False),
                       "source_url": manifest.get("source_url"),
                       "pdf_pages": sorted({u.block["pdf_page"] for u in units}),
                       "source_block_ids": list(dict.fromkeys(u.block["block_id"] for u in units)),
                       "source_spans": spans, "text": text,
                       "search_text": re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip(),
                       "char_count": len(text), "verification_status": "unverified",
                       "chunker_version": CHUNKER_VERSION, "chunking_config": asdict(config),
                       "extraction_fingerprint": document.extraction_fingerprint,
                       "notes": sorted({note for u in units for note in u.notes})})

    for page in sorted(grouped):
        pending = []
        for unit in grouped[page]:
            heading_break = (config.break_on_headings and _heading(unit.text) and pending and
                             any(not _heading(u.text) for u in pending))
            if heading_break:
                emit(pending)
                pending = []
            if pending and _length([*pending, unit]) > config.max_chars:
                emit(pending)
                pending = _overlap(pending, config.overlap_chars)
                while pending and _length([*pending, unit]) > config.max_chars:
                    pending.pop(0)
            pending.append(unit)
        if pending:
            emit(pending)
    return chunks


def validate_chunks(document, chunks, config):
    """Check complete source coverage and exact source-text reconstruction."""
    original = {b["block_id"]: b for b in document.blocks}
    ranges = defaultdict(list)
    ids = set()
    for chunk in chunks:
        if chunk["chunk_id"] in ids:
            raise DataValidationError("중복 chunk_id")
        ids.add(chunk["chunk_id"])
        if (chunk["doc_id"] != document.manifest["doc_id"] or
            chunk["version_sha256"] != document.manifest["version_sha256"]):
            raise DataValidationError("청크의 문서/버전 불일치")
        if len(chunk["text"]) > config.max_chars:
            raise DataValidationError("청크 최대 문자 수 초과")
        if chunk["char_count"] != len(chunk["text"]):
            raise DataValidationError("청크 문자 수 불일치")
        if chunk["source_block_ids"] != list(dict.fromkeys(s["block_id"] for s in chunk["source_spans"])):
            raise DataValidationError("청크의 블록 참조 불일치")
        if chunk["pdf_pages"] != sorted({s["pdf_page"] for s in chunk["source_spans"]}):
            raise DataValidationError("청크의 페이지 참조 불일치")
        fragments = []
        for span in chunk["source_spans"]:
            block = original.get(span["block_id"])
            start, end = span["start_char"], span["end_char"]
            if block is None or not 0 <= start < end <= len(block["text"]):
                raise DataValidationError("잘못된 원문 구간")
            if span["pdf_page"] != block["pdf_page"] or span["source_locator"] != block["source_locator"]:
                raise DataValidationError("원문 페이지/좌표 불일치")
            fragments.append(block["text"][start:end])
            ranges[block["block_id"]].append((start, end))
        if "\n".join(fragments) != chunk["text"]:
            raise DataValidationError("청크 텍스트와 원문 구간 불일치")
    covered = 0
    for bid, block in original.items():
        cursor = 0
        for start, end in sorted(ranges.get(bid, [])):
            if start > cursor:
                raise DataValidationError(f"원문 구간 누락: {bid}")
            cursor = max(cursor, end)
        if cursor != len(block["text"]):
            raise DataValidationError(f"원문 블록 누락: {bid}")
        covered += cursor
    return {"covered_blocks": len(ranges), "covered_source_chars": covered,
            "chunk_ids_unique": True, "source_text_reconstruction": True, "all_source_chars_covered": True}


def _atomic_text(path, text):
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
            name = f.name
            f.write(text)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def prepare_dataset(input_dir, out=None, config=None):
    config = config or ChunkingConfig()
    document = load_document(input_dir)
    chunks = build_chunks(document, config)
    validation = validate_chunks(document, chunks, config)
    output = Path(out).expanduser().resolve() if out is not None else (
        PROJECT_ROOT / "data/processed/rag" / document.input_dir.name / config.config_id)
    if output == document.input_dir:
        raise ValueError("출력은 추출 원본 폴더와 별도로 지정하세요.")
    # Avoid replacing extraction inputs even if an output path was mistyped.
    output.mkdir(parents=True, exist_ok=True)
    rows = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks)
    per_page = defaultdict(int)
    for chunk in chunks:
        for page in chunk["pdf_pages"]:
            per_page[page] += 1
    report = {"schema_version": CHUNK_SCHEMA, "doc_id": document.manifest["doc_id"],
              "version_sha256": document.manifest["version_sha256"],
              "input_dir": str(document.input_dir), "output_dir": str(output),
              "input_hashes": document.input_hashes, "extraction_fingerprint": document.extraction_fingerprint,
              "chunker_version": CHUNKER_VERSION, "config_id": config.config_id, "config": asdict(config),
              "page_count": len(document.pages), "block_count": len(document.blocks), "chunk_count": len(chunks),
              "min_chunk_chars": min((c["char_count"] for c in chunks), default=0),
              "max_chunk_chars": max((c["char_count"] for c in chunks), default=0),
              "no_block_pages": [p["pdf_page"] for p in document.pages if p["pdf_page"] not in per_page],
              "chunks_per_page": dict(per_page), "validation": validation,
              "oversized_split_chunks": sum("oversized_block_split" in c["notes"] for c in chunks),
              "hard_split_chunks": sum("hard_character_split" in c["notes"] for c in chunks),
              "limitations": ["character_count_not_token_count", "same_page_only",
                              "heading_breaks_are_conservative_regex_heuristics",
                              "whole_block_overlap_up_to_budget_may_be_zero",
                              "table_structure_and_semantics_not_verified", "semantic_sections_not_guaranteed",
                              "no_retrieval_index_or_embeddings_yet"]}
    _atomic_text(output / "chunks.jsonl", rows)
    _atomic_text(output / "chunking_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="manifest/pages/blocks가 있는 폴더")
    parser.add_argument("--out", type=Path, help="별도의 결과 폴더. 기본값은 data/processed/rag 아래")
    parser.add_argument("--max-chars", type=int, default=1000)
    parser.add_argument("--overlap-chars", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        root = args.input_dir if args.input_dir.is_absolute() else PROJECT_ROOT / args.input_dir
        out = args.out if args.out is None or args.out.is_absolute() else PROJECT_ROOT / args.out
        report = prepare_dataset(root, out, ChunkingConfig(args.max_chars, args.overlap_chars))
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({k: report[k] for k in ["doc_id", "page_count", "block_count", "chunk_count", "max_chunk_chars",
                                            "no_block_pages", "config_id", "output_dir", "validation"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
