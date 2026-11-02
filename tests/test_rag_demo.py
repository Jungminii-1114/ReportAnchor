"""웹 데모의 검색·N-04 분리와 원문 연결을 실제 모델 없이 검사한다."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rag.nodes.draft_answer import DraftAnswerNode
from scripts.run_rag_demo import Demo


class DemoDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        texts = ["2025년 경상운영비는 4,566백만원이다.", "인건비 운영 계획", "정보시스템 유지보수 계획"]
        blocks, chunks = [], []
        for number, text in enumerate(texts, 1):
            locator = dict(type="pdf_page_bbox", pdf_page=number, bbox=[0, 0, 100, 10],
                           units="pt", coordinate_space="pymupdf_unrotated_page")
            block = dict(block_id=f"b{number}", doc_id="demo", version_sha256="v1",
                         pdf_page=number, text=text, source_locator=locator,
                         extraction_method="native_text", verification_status="unverified")
            span = dict(block_id=block["block_id"], pdf_page=number, start_char=0,
                        end_char=len(text), source_locator=locator)
            blocks.append(block)
            chunks.append(dict(chunk_id=f"c{number}", doc_id="demo", version_sha256="v1",
                               text=text, search_text=text, pdf_pages=[number], source_spans=[span],
                               source_block_ids=[block["block_id"]], document_title="데모 문서"))
        cls.chunks_path, cls.blocks_path = root / "chunks.jsonl", root / "blocks.jsonl"
        cls.chunks_path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chunks))
        cls.blocks_path.write_text("\n".join(json.dumps(b, ensure_ascii=False) for b in blocks))
        cls.demo = Demo(cls.chunks_path, cls.blocks_path, None)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.demo.sessions.clear()
        self.demo.draft_node = DraftAnswerNode("test-model", "http://localhost:11434")
        self.no_network = patch("rag.nodes.draft_answer.urlopen", side_effect=AssertionError("실제 모델 호출 금지"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def search(self):
        return self.demo.search("2025년 경상운영비는 얼마인가?", 3)

    def model_response(self, result):
        evidence = result["source_context"]["data"]["items"][0]
        return json.dumps(dict(claims=[dict(text="2025년 경상운영비는 4,566백만원이다.",
            citations=[dict(evidence_id=evidence["evidence_id"], quote="2025년 경상운영비는 4,566백만원")])],
            missing_information=[]), ensure_ascii=False)

    def test_search_does_not_call_model(self):
        with patch.object(self.demo.draft_node, "run") as run:
            result = self.search()
            run.assert_not_called()
        self.assertIsNone(result["draft"])
        self.assertTrue(result["sources"])
        self.assertEqual(result["source_context"]["status"], "success")

    def test_draft_uses_cached_question_and_exact_n02_bundle(self):
        result = self.search()
        request_ref = result["retrieval"]["data"]["request_ref"]
        session = self.demo.sessions[request_ref]
        before = asdict(session["context"].data)
        with patch.object(self.demo.draft_node, "_call_ollama", return_value=self.model_response(result)):
            with patch.object(self.demo.draft_node, "run", wraps=self.demo.draft_node.run) as run:
                answer = self.demo.draft(request_ref)
                self.assertIs(run.call_args.args[1], session["context"].data)
                self.assertEqual(run.call_args.args[0], result["query"])
        self.assertEqual(answer["draft"]["status"], "success")
        self.assertEqual(asdict(session["context"].data), before)
        self.assertEqual(answer["generation"]["semantic_verification"], "not_implemented")

    def test_successful_repeated_request_does_not_bill_again(self):
        result = self.search()
        reference = result["retrieval"]["data"]["request_ref"]
        with patch.object(self.demo.draft_node, "_call_ollama", return_value=self.model_response(result)) as model:
            first = self.demo.draft(reference)
            second = self.demo.draft(reference)
            model.assert_called_once()
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["draft"], second["draft"])

    def test_generation_error_keeps_search_sources_and_can_retry(self):
        result = self.search()
        reference = result["retrieval"]["data"]["request_ref"]
        with patch.object(self.demo.draft_node, "_call_ollama", return_value="not JSON"):
            answer = self.demo.draft(reference)
        self.assertEqual(answer["draft"]["status"], "error")
        self.assertTrue(result["sources"])
        self.assertIsNone(self.demo.sessions[reference]["draft"])

    def test_unknown_request_and_disabled_generation_are_rejected(self):
        with self.assertRaises(ValueError):
            self.demo.draft("unknown")
        self.demo.draft_node = None
        self.assertFalse(self.demo.info()["generation"]["enabled"])
        with self.assertRaises(ValueError):
            self.demo.draft("unknown")

    def test_session_limit_and_expiry(self):
        first = self.search()["retrieval"]["data"]["request_ref"]
        for _ in range(16):
            self.search()
        self.assertEqual(len(self.demo.sessions), 16)
        self.assertNotIn(first, self.demo.sessions)
        with self.assertRaises(ValueError):
            self.demo.draft(first)

    def test_cloud_key_is_not_exposed_in_info_or_response(self):
        self.demo.draft_node = DraftAnswerNode("test-cloud", "https://ollama.com", provider="cloud", api_key="mock-private-key")
        result = self.search()
        with patch.object(self.demo.draft_node, "_call_ollama", return_value=self.model_response(result)):
            answer = self.demo.draft(result["retrieval"]["data"]["request_ref"])
        self.assertNotIn("mock-private-key", json.dumps(self.demo.info()))
        self.assertNotIn("mock-private-key", json.dumps(answer))
        self.assertIn("Ollama Cloud", self.demo.info()["generation"]["data_handling"])

    def test_empty_results_abstain_without_calling_model(self):
        result = self.demo.search("zzzxqv999xxxyyy", 3)
        reference = result["retrieval"]["data"]["request_ref"]
        with patch.object(self.demo.draft_node, "_call_ollama") as model:
            answer = self.demo.draft(reference)
            model.assert_not_called()
        self.assertEqual(answer["draft"]["outcome"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
