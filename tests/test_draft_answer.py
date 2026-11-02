"""N-04의 결과 반환·오류 경로를 모델 호출 없이 검사한다."""
from copy import deepcopy
from dataclasses import asdict
import io
import json
import unittest
import tempfile
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from rag.contracts import (
    ContextExpansionConfig, EvidenceAnchor, EvidenceBundle, EvidenceItem,
    NodeResult, ReviewDraft, SourceSpan,
)
from rag.nodes.draft_answer import DraftAnswerNode, load_evidence_bundle, main


class DraftAnswerTests(unittest.TestCase):
    def setUp(self):
        self.text = "2025년 경상운영비는 4,566백만원이며, 전년 대비 34백만원 증가하였다."
        span = SourceSpan("b1", 1, 0, len(self.text), self.text, {}, "synthetic", "unverified")
        item = EvidenceItem(
            "evidence_demo_001", "demo_doc", "demo_version", [1], self.text,
            [span], [EvidenceAnchor("c1", 1, self.text, [span])], "unverified",
        )
        self.bundle = EvidenceBundle(
            "demo_request", [item], ContextExpansionConfig(), len(self.text),
            1, 1, [], False,
        )
        self.question = "2025년 경상운영비는 얼마인가?"
        self.payload = {
            "claims": [{
                "text": "2025년 경상운영비는 4,566백만원이다.",
                "citations": [{
                    "evidence_id": item.evidence_id,
                    "quote": "2025년 경상운영비는 4,566백만원",
                }],
            }],
            "missing_information": [],
        }
        self.raw = json.dumps(self.payload, ensure_ascii=False)
        self.node = DraftAnswerNode("test-model", "http://localhost:11434")
        # 각 테스트가 가짜 응답을 지정한 경우 외에는 통신을 금지한다.
        self.transport = patch(
            "rag.nodes.draft_answer.urlopen",
            side_effect=AssertionError("실제 API 호출은 금지되어 있습니다."),
        ).start()
        self.addCleanup(patch.stopall)

    def run_with_response(self, response):
        with patch.object(self.node, "_call_ollama", return_value=response):
            return self.node.run(self.question, self.bundle)

    def test_success_returns_node_result_and_preserves_inputs(self):
        before = asdict(self.bundle)
        result = self.run_with_response(self.raw)
        self.assertIsInstance(result, NodeResult)
        self.assertIsInstance(result.data, ReviewDraft)
        self.assertEqual(result.status, "success")
        self.assertIsNone(result.outcome)
        self.assertEqual(result.data.request_ref, "demo_request")
        self.assertEqual(result.data.question, self.question)
        self.assertEqual(result.data.claims[0].claim_id, "claim_0001")
        self.assertEqual(result.data.claims[0].citations[0].quote,
                         self.payload["claims"][0]["citations"][0]["quote"])
        self.assertEqual(result.diagnostics["parse_and_validate_draft"].count, 1)
        self.assertEqual(asdict(self.bundle), before)
        json.dumps(asdict(result), ensure_ascii=False)

    def test_input_errors_never_call_model(self):
        with patch.object(self.node, "_call_ollama") as model:
            for question, bundle in [(" ", self.bundle), (None, self.bundle),
                                     (self.question, None), (self.question, {})]:
                with self.subTest(question=question, bundle_type=type(bundle).__name__):
                    result = self.node.run(question, bundle)
                    self.assertEqual(result.status, "error")
                    self.assertIsNone(result.data)
                    self.assertFalse(result.diagnostics["validate_draft_input"].retryable)
            model.assert_not_called()

    def test_malformed_and_duplicate_evidence_rejected_before_call(self):
        for variant, expected in [("wrong_item", "INVALID_EVIDENCE_ITEM"),
                                  ("empty_text", "INVALID_EVIDENCE_ITEM"),
                                  ("duplicate", "DUPLICATE_EVIDENCE_ID")]:
            bundle = deepcopy(self.bundle)
            if variant == "wrong_item":
                bundle.items = [{}]
            elif variant == "empty_text":
                bundle.items[0].text = " "
            else:
                bundle.items.append(deepcopy(bundle.items[0]))
            with self.subTest(variant=variant), patch.object(self.node, "_call_ollama") as model:
                result = self.node.run(self.question, bundle)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.diagnostics["validate_draft_input"].error_code, expected)
                model.assert_not_called()

    def test_empty_bundle_abstains_without_model_or_key(self):
        bundle = deepcopy(self.bundle)
        bundle.items = []
        bundle.char_count = bundle.requested_candidates = bundle.resolved_candidates = 0
        self.node = DraftAnswerNode("test-model", "https://ollama.com", provider="cloud")
        with patch.object(self.node, "_call_ollama") as model:
            result = self.node.run(self.question, bundle)
            model.assert_not_called()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.outcome, "insufficient_evidence")
        self.assertEqual(result.data.claims, [])
        self.assertTrue(result.data.missing_information)
        self.assertEqual(result.diagnostics["call_ollama"].status, "skipped")

    def test_model_abstention_is_success_not_transport_failure(self):
        result = self.run_with_response(json.dumps({
            "claims": [], "missing_information": ["관련 근거가 부족합니다."],
        }))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.outcome, "insufficient_evidence")
        self.assertEqual(result.diagnostics["call_ollama"].status, "success")

    def test_invalid_configuration_never_calls_model(self):
        for settings in [dict(provider="unknown"), dict(model=" "),
                         dict(timeout=0), dict(timeout=True), dict(timeout=float("inf")),
                         dict(base_url="https://ollama.com/api/chat")]:
            self.node = DraftAnswerNode("test-model", "http://localhost:11434")
            for key, value in settings.items():
                setattr(self.node, key, value)
            with self.subTest(settings=settings), patch.object(self.node, "_call_ollama") as model:
                result = self.node.run(self.question, self.bundle)
                self.assertEqual(result.status, "error")
                self.assertIn("validate_model_config", result.diagnostics)
                model.assert_not_called()

    def test_cloud_key_required_and_host_validated(self):
        for base_url, api_key, code in [
            ("https://ollama.com", None, "MISSING_API_KEY"),
            ("http://ollama.com", "mock-key", "INVALID_MODEL_URL"),
            ("https://example.invalid", "mock-key", "INVALID_MODEL_URL"),
        ]:
            self.node = DraftAnswerNode("test-model", base_url, provider="cloud", api_key=api_key)
            with self.subTest(url=base_url), patch.object(self.node, "_call_ollama") as model:
                result = self.node.run(self.question, self.bundle)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.diagnostics["validate_model_config"].error_code, code)
                model.assert_not_called()

    def test_http_errors_report_retryability_without_raw_details(self):
        for code, retryable in [(401, False), (404, False), (429, True), (503, True)]:
            error = HTTPError("https://ollama.com/api/chat", code, "private detail", {}, None)
            with self.subTest(code=code), patch.object(self.node, "_call_ollama", side_effect=error):
                result = self.node.run(self.question, self.bundle)
                diagnostic = result.diagnostics["call_ollama"]
                self.assertEqual(result.status, "error")
                self.assertIsNone(result.data)
                self.assertEqual(diagnostic.error_code, "MODEL_HTTP_ERROR")
                self.assertEqual(diagnostic.retryable, retryable)
                self.assertNotIn("private detail", json.dumps(asdict(result)))
                self.assertNotIn(self.text, json.dumps(asdict(result), ensure_ascii=False))

    def test_timeout_and_connection_errors(self):
        for error, code in [
            (TimeoutError(), "MODEL_TIMEOUT"),
            (URLError(TimeoutError()), "MODEL_TIMEOUT"),
            (URLError("offline"), "MODEL_CONNECTION_FAILED"),
            (ConnectionResetError(), "MODEL_CONNECTION_FAILED"),
        ]:
            with self.subTest(error=type(error).__name__), patch.object(self.node, "_call_ollama", side_effect=error):
                result = self.node.run(self.question, self.bundle)
                diagnostic = result.diagnostics["call_ollama"]
                self.assertEqual(diagnostic.error_code, code)
                self.assertTrue(diagnostic.retryable)

    def test_malformed_server_response(self):
        for response_body in [{"message": {}}, {"message": {"content": " "}}, []]:
            self.transport.side_effect = None
            self.transport.return_value = io.BytesIO(json.dumps(response_body).encode())
            with self.subTest(response=response_body):
                result = self.node.run(self.question, self.bundle)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.diagnostics["call_ollama"].error_code, "INVALID_MODEL_RESPONSE")

    def test_json_schema_and_citations_fail_closed(self):
        bad_cases = ["not JSON", "[]"]
        for update in [
            lambda d: d.pop("claims"),
            lambda d: d.update(missing_information="none"),
            lambda d: d.update(claims=[]),
            lambda d: d["claims"][0].update(text=" "),
            lambda d: d["claims"][0].update(citations=[]),
            lambda d: d["claims"][0]["citations"][0].update(evidence_id="unknown"),
            lambda d: d["claims"][0]["citations"][0].update(quote="9,999백만원"),
        ]:
            data = deepcopy(self.payload)
            update(data)
            bad_cases.append(json.dumps(data, ensure_ascii=False))
        for raw in bad_cases:
            with self.subTest(raw=raw):
                result = self.run_with_response(raw)
                self.assertEqual(result.status, "error")
                self.assertIsNone(result.data)
                self.assertEqual(result.diagnostics["call_ollama"].status, "success")
                self.assertEqual(result.diagnostics["parse_and_validate_draft"].error_code, "INVALID_DRAFT_RESPONSE")

    def test_real_transport_shape_and_auth_with_fake_server(self):
        self.node = DraftAnswerNode("test-model", "https://ollama.com", provider="cloud", api_key="mock-key")
        self.transport.side_effect = None
        self.transport.return_value = io.BytesIO(json.dumps({"message": {"content": self.raw}}).encode())
        result = self.node.run(self.question, self.bundle)
        self.assertEqual(result.status, "success")
        request = self.transport.call_args.args[0]
        self.assertEqual(request.full_url, "https://ollama.com/api/chat")
        self.assertEqual(request.get_header("Authorization"), "Bearer mock-key")
        self.assertEqual(json.loads(request.data)["stream"], False)
        self.assertEqual(self.transport.call_args.kwargs["timeout"], 120.0)
        self.assertNotIn("mock-key", json.dumps(asdict(result)))

    def test_programming_errors_are_not_silently_hidden(self):
        with patch.object(self.node, "_build_prompt", side_effect=RuntimeError("bug")):
            with self.assertRaises(RuntimeError):
                self.node.run(self.question, self.bundle)


    def test_load_bundle_and_node_result_preserve_nested_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "n02.json"
            for payload in [asdict(self.bundle), {"status": "success", "data": asdict(self.bundle)},
                            {"status": "partial", "data": asdict(self.bundle)}]:
                with self.subTest(wrapped="status" in payload):
                    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    loaded = load_evidence_bundle(path)
                    self.assertEqual(asdict(loaded), asdict(self.bundle))
                    self.assertIsInstance(loaded.items[0].source_spans[0], SourceSpan)
                    self.assertIsInstance(loaded.items[0].anchors[0], EvidenceAnchor)

    def test_invalid_n02_file_rejected(self):
        cases = [[], {"status": "error", "data": None}]
        for change in [
            lambda d: d.update(char_count=1),
            lambda d: d.update(resolved_candidates=5),
            lambda d: d["items"][0].update(text="changed"),
            lambda d: d["items"][0]["source_spans"][0].update(end_char=9999),
        ]:
            payload = asdict(self.bundle)
            change(payload)
            cases.append(payload)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "n02.json"
            for payload in cases:
                with self.subTest(payload_type=type(payload).__name__):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises((ValueError, TypeError, KeyError)):
                        load_evidence_bundle(path)

    def cli(self, path, provider="local"):
        arguments = ["draft_answer", "--provider", provider, "--model", "test-model",
                     "--query", self.question, "--evidence-bundle", str(path)]
        output = io.StringIO()
        with patch("sys.argv", arguments), redirect_stdout(output):
            code = main()
        return code, json.loads(output.getvalue())

    def test_cli_prints_json_and_returns_zero_on_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "n02.json"
            path.write_text(json.dumps({"status": "success", "data": asdict(self.bundle)}))
            with patch.object(DraftAnswerNode, "_call_ollama", return_value=self.raw) as model:
                code, result = self.cli(path)
                model.assert_called_once()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["request_ref"], self.bundle.request_ref)
        self.assertEqual(result["data"]["claims"][0]["citations"][0]["evidence_id"], "evidence_demo_001")

    def test_cli_missing_file_returns_error_without_model(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(DraftAnswerNode, "_call_ollama") as model:
            code, result = self.cli(Path(directory) / "missing.json")
            model.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(result["diagnostics"]["load_evidence_bundle"]["error_code"], "INVALID_EVIDENCE_FILE")

    def test_cli_cloud_missing_key_is_structured_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "n02.json"
            path.write_text(json.dumps(asdict(self.bundle)))
            with patch("rag.nodes.draft_answer.os.environ", {}), patch.object(DraftAnswerNode, "_call_ollama") as model:
                code, result = self.cli(path, "cloud")
                model.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(result["diagnostics"]["validate_model_config"]["error_code"], "MISSING_API_KEY")

    def test_cli_cloud_response_is_validated_and_key_not_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "n02.json"
            path.write_text(json.dumps(asdict(self.bundle)))
            with patch("rag.nodes.draft_answer.os.environ", {"OLLAMA_API_KEY": "mock-cli-key"}):
                with patch.object(DraftAnswerNode, "_call_ollama", return_value=self.raw):
                    code, result = self.cli(path, "cloud")
        self.assertEqual(code, 0)
        self.assertNotIn("mock-cli-key", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
