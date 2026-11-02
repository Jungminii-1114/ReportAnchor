"""RAG data preparation contracts: coverage, boundaries, provenance, repeatability."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag_data import (ChunkingConfig, DataValidationError, build_chunks,
                      load_document, prepare_dataset, validate_chunks)


class RagDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "input"
        self.data.mkdir()
        self.sha = "a" * 64
        self.doc = "doc_demo"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, page_texts):
        manifest = {"doc_id": self.doc, "version_sha256": self.sha, "page_count": len(page_texts),
                    "document_title": "demo", "source_url": None,
                    "metadata": {"parser": "demo", "parser_version": "1"}}
        pages, blocks = [], []
        for page, texts in enumerate(page_texts, 1):
            text = "\n".join(texts)
            pages.append({"doc_id": self.doc, "version_sha256": self.sha,
                          "pdf_page": page, "text": text, "char_count": len(text.strip())})
            for index, value in enumerate(texts):
                blocks.append({"doc_id": self.doc, "version_sha256": self.sha,
                               "block_id": f"p{page}b{index}", "block_index": index,
                               "pdf_page": page, "text": value,
                               "source_locator": {"pdf_page": page, "bbox": [1, index * 10, 100, index * 10 + 9]}})
        self.save("manifest.json", manifest)
        self.save("pages.jsonl", pages)
        self.save("blocks.jsonl", blocks)
        return pages, blocks

    def save(self, name, value):
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in value) if name.endswith("jsonl") else json.dumps(value, ensure_ascii=False)
        (self.data / name).write_text(text, encoding="utf-8")

    def test_original_reading_order_is_not_block_index_order(self):
        _, blocks = self.write([["첫 문장", "둘째 문장"], []])
        blocks[0]["block_index"] = 90
        blocks[1]["block_index"] = 2
        self.save("blocks.jsonl", blocks)
        chunks = build_chunks(load_document(self.data))
        self.assertEqual(chunks[0]["text"], "첫 문장\n둘째 문장")
        self.assertEqual(chunks[0]["source_block_ids"], ["p1b0", "p1b1"])

    def test_never_cross_pages_or_explicit_heading_boundaries(self):
        self.write([["1. 사업 A", "A의 목적과 계획", "2. 사업 B", "B의 목적과 계획"], ["다음 페이지의 본문"]])
        chunks = build_chunks(load_document(self.data))
        self.assertEqual([c["pdf_pages"] for c in chunks], [[1], [1], [2]])
        self.assertNotIn("사업 B", chunks[0]["text"])
        self.assertIn("사업 B", chunks[1]["text"])

    def test_complete_block_overlap_and_full_coverage(self):
        self.write([[(chr(65+i) * 10) for i in range(8)], []])
        data = load_document(self.data)
        config = ChunkingConfig(64, 12)
        chunks = build_chunks(data, config)
        self.assertGreater(len(chunks), 1)
        shared = set(chunks[0]["source_block_ids"]) & set(chunks[1]["source_block_ids"])
        self.assertEqual(shared, {"p1b4"})
        self.assertTrue(all(c["char_count"] <= 64 for c in chunks))
        self.assertTrue(validate_chunks(data, chunks, config)["all_source_chars_covered"])
        self.assertEqual({b for c in chunks for b in c["source_block_ids"]}, {b["block_id"] for b in data.blocks})

    def test_long_unicode_block_preserves_every_character_and_offsets(self):
        text = "가" * 130 + " 목적과 계획입니다.\n" * 18
        self.write([[text]])
        data = load_document(self.data)
        config = ChunkingConfig(64, 12)
        chunks = build_chunks(data, config)
        covered = set()
        for c in chunks:
            parts = []
            for s in c["source_spans"]:
                parts.append(text[s["start_char"]:s["end_char"]])
                covered.update(range(s["start_char"], s["end_char"]))
            self.assertEqual(c["text"], "\n".join(parts))
            self.assertLessEqual(len(c["text"]), 64)
        self.assertEqual(covered, set(range(len(text))))
        self.assertTrue(any("hard_character_split" in c["notes"] for c in chunks))
        self.assertTrue(validate_chunks(data, chunks, config)["source_text_reconstruction"])

    def test_ids_are_repeatable_and_config_and_input_sensitive(self):
        self.write([["같은 데이터의 첫 문단", "두 번째 문단"]])
        data = load_document(self.data)
        first = build_chunks(data)
        self.assertEqual(first, build_chunks(data))
        moved = self.root / "moved"
        shutil.copytree(self.data, moved)
        self.assertEqual(first, build_chunks(load_document(moved)))
        changed = build_chunks(data, ChunkingConfig(800, 120))
        self.assertNotEqual(first[0]["chunk_id"], changed[0]["chunk_id"])
        self.write([["다른 내용의 첫 문단", "두 번째 문단"]])
        self.assertNotEqual(first[0]["chunk_id"], build_chunks(load_document(self.data))[0]["chunk_id"])

    def test_loader_rejects_mixed_doc_version_duplicate_id_and_bad_locator(self):
        for patch in [{"doc_id": "another"}, {"version_sha256": "b"*64},
                      {"pdf_page": 3}, {"source_locator": {"pdf_page": 2}},
                      {"source_locator": {"pdf_page": 1, "bbox": [1, 2, 0, 1]}}]:
            with self.subTest(patch=patch):
                _, blocks = self.write([["첫 블록", "둘째 블록"]])
                blocks[0].update(patch)
                self.save("blocks.jsonl", blocks)
                with self.assertRaises(DataValidationError):
                    load_document(self.data)
        _, blocks = self.write([["첫 블록", "둘째 블록"]])
        blocks[1]["block_id"] = blocks[0]["block_id"]
        self.save("blocks.jsonl", blocks)
        with self.assertRaisesRegex(DataValidationError, "중복"):
            load_document(self.data)

    def test_loader_rejects_missing_pages_blocks_and_malformed_jsonl(self):
        pages, _ = self.write([["본문"], []])
        self.save("pages.jsonl", pages[:1])
        with self.assertRaisesRegex(DataValidationError, "전체 페이지"):
            load_document(self.data)
        self.write([["본문"]])
        self.save("blocks.jsonl", [])
        with self.assertRaisesRegex(DataValidationError, "존재 여부"):
            load_document(self.data)
        (self.data / "blocks.jsonl").write_text("{broken}\n")
        with self.assertRaisesRegex(DataValidationError, "blocks.jsonl:1"):
            load_document(self.data)

    def test_empty_document_and_original_text_vs_search_text(self):
        self.write([[], []])
        data = load_document(self.data)
        self.assertEqual(build_chunks(data), [])
        self.assertTrue(validate_chunks(data, [], ChunkingConfig())["all_source_chars_covered"])
        self.write([["Ａ  사업\n  23.6%\n"]])
        chunk = build_chunks(load_document(self.data))[0]
        self.assertEqual(chunk["text"], "Ａ  사업\n  23.6%\n")
        self.assertEqual(chunk["search_text"], "A 사업 23.6%")
        self.assertEqual(chunk["verification_status"], "unverified")

    def test_validation_detects_tampered_text_location_or_missing_chunk(self):
        self.write([["처음 문장", "다음 문장"]])
        data = load_document(self.data)
        chunks = build_chunks(data)
        broken = copy.deepcopy(chunks)
        broken[0]["text"] = "잘못된 내용"
        with self.assertRaises(DataValidationError):
            validate_chunks(data, broken, ChunkingConfig())
        broken = copy.deepcopy(chunks)
        broken[0]["source_spans"][0]["source_locator"]["bbox"][0] = 999
        with self.assertRaises(DataValidationError):
            validate_chunks(data, broken, ChunkingConfig())
        self.assertEqual(data.blocks[0]["source_locator"]["bbox"][0], 1)
        broken = copy.deepcopy(chunks)
        broken[0]["source_spans"][0]["pdf_page"] = 2
        with self.assertRaises(DataValidationError):
            validate_chunks(data, broken, ChunkingConfig())
        with self.assertRaisesRegex(DataValidationError, "누락"):
            validate_chunks(data, [], ChunkingConfig())

    def test_cli_preserves_inputs_and_does_not_need_pdf_or_model(self):
        self.write([["추출된 텍스트만 있으면 됩니다."], []])
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.data.iterdir()}
        output = self.root / "result"
        process = subprocess.run([sys.executable, str(ROOT / "02_build_chunks.py"),
                                  "--input-dir", str(self.data), "--out", str(output)],
                                 cwd=self.root, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["block_count"], 1)
        self.assertTrue((output / "chunks.jsonl").is_file())
        report = json.loads((output / "chunking_report.json").read_text())
        self.assertEqual(report["no_block_pages"], [2])
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.data.iterdir()})
        with self.assertRaisesRegex(ValueError, "별도로"):
            prepare_dataset(self.data, self.data)


if __name__ == "__main__":
    unittest.main()
