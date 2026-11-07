"""N-03 orchestration/CLI regressions. No live model or network calls."""
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from rag.contracts import (ClaimVerification, DraftClaim, EvidenceBundle, EvidenceItem,
                           FailedItem, NodeResult, ReviewDraft, StepDiagnostic, Verdict)
from rag.llm.ollama_client import OllamaClient
from rag.nodes.verify_grounding import VerifyGroundingNode, load_review_draft, main


class GroundingFixture(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient("test-model", "http://localhost:11434")
        self.node = VerifyGroundingNode(self.client)
        self.claim = DraftClaim("claim_0007", "사업비는 20백만원 증가했다.", ["c1"])
        self.evidence = EvidenceItem("c1", "doc1", "v1", 1,
            "2024년 사업비는 100백만원, 2025년 사업비는 120백만원이다.",
            [{"block_id": "b1", "source_locator": {"pdf_page": 3}}], False)
        self.draft = ReviewDraft("req", [self.claim], [])
        self.bundle = EvidenceBundle("req", [self.evidence], "test-profile")
        guard = patch("rag.llm.ollama_client.urlopen", side_effect=AssertionError("No live calls"))
        guard.start(); self.addCleanup(guard.stop)

    def response(self, claim_id="claim_0007", verdict="supported", reason="120 - 100 = 20백만원이다."):
        return json.dumps(dict(claim_id=claim_id, verdict=verdict, reason=reason), ensure_ascii=False)

    def run_response(self, responses=None, **kwargs):
        with patch.object(self.client, "chat", side_effect=responses or [self.response()]) as model:
            result = self.node.run("req", self.draft, self.bundle, **kwargs)
        return result, model


class GroundingTests(GroundingFixture):
    def test_all_verdicts_are_successful_execution(self):
        for verdict in Verdict:
            with self.subTest(verdict=verdict):
                result, model = self.run_response([self.response(verdict=verdict.value)])
                self.assertEqual(result.status, "success")
                self.assertEqual(result.data.checks[0].verdict, verdict)
                self.assertIsNone(result.outcome)
                model.assert_called_once()

    def test_partial_parse_failure_keeps_other_checks(self):
        self.draft.claims.append(DraftClaim("c_other", "다른 주장", ["c1"]))
        result, model = self.run_response([self.response(), "bad json"])
        self.assertEqual(result.status, "partial")
        self.assertEqual([c.claim_id for c in result.data.checks], ["claim_0007"])
        self.assertEqual(result.diagnostics["verify_claims"].failed_items[0].ref, "c_other")
        self.assertEqual(model.call_count, 2)

    def test_all_failed_has_no_business_report(self):
        result, _ = self.run_response(["invalid"])
        self.assertEqual(result.status, "error")
        self.assertIsNone(result.data)
        self.assertFalse(result.diagnostics["verify_claims"].failed_items[0].retryable)

    def test_unreferenced_claim_is_skipped_not_insufficient(self):
        self.claim.evidence_refs = []
        self.client.model = ""  # no calls, so model configuration is irrelevant
        result, model = self.run_response()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.data.checks, [])
        self.assertEqual(result.diagnostics["select_claims"].excluded_refs, [self.claim.claim_id])
        self.assertEqual(result.diagnostics["call_model"].status, "skipped")
        model.assert_not_called()

    def test_empty_draft_and_bundle_skip_model(self):
        self.draft.claims = []; self.bundle.evidence_items = []
        result, model = self.run_response()
        self.assertEqual((result.status, result.data.checks), ("success", []))
        model.assert_not_called()

    def test_missing_reference_is_item_error_not_verdict(self):
        self.draft.claims.insert(0, DraftClaim("missing_claim", "누락 주장", ["missing"]))
        result, model = self.run_response()
        self.assertEqual(result.status, "partial")
        failure = result.diagnostics["verify_claims"].failed_items[0]
        self.assertEqual((failure.ref, failure.error_code), ("missing_claim", "EVIDENCE_LOOKUP_FAILED"))
        self.assertEqual(len(result.data.checks), 1)
        model.assert_called_once()

    def test_input_mismatch_and_duplicates_block_all_calls(self):
        for kind in ("request", "claim", "evidence"):
            draft, bundle = deepcopy(self.draft), deepcopy(self.bundle)
            if kind == "request": draft.request_ref = "other"
            if kind == "claim": draft.claims.append(deepcopy(draft.claims[0]))
            if kind == "evidence": bundle.evidence_items.append(deepcopy(bundle.evidence_items[0]))
            with self.subTest(kind=kind), patch.object(self.client, "chat") as model:
                result = self.node.run("req", draft, bundle)
                self.assertEqual(result.status, "error"); model.assert_not_called()

    def test_optional_numeric_failure_does_not_fail_claim(self):
        with patch.object(self.node, "_collect_numeric_hints", side_effect=RuntimeError("private raw text")):
            result, model = self.run_response()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.diagnostics["collect_numeric_hints"].status, "failed")
        self.assertEqual(result.diagnostics["verify_claims"].failed_items, [])
        self.assertEqual(json.loads(model.call_args.kwargs["messages"][1]["content"])["numeric_hints"], {})
        self.assertNotIn("private raw text", json.dumps(asdict(result)))

    def test_numeric_claim_is_not_added_once_per_source(self):
        hints = self.node._collect_numeric_hints(self.claim, [self.evidence, self.evidence])
        self.assertEqual(hints["claim_numbers"], ["20"])
        self.assertEqual(hints["numbers_not_found"], ["20"])
        self.assertEqual(hints["evidence_numbers"].count("120"), 2)

    def test_calculated_number_reaches_model_without_rule_based_verdict(self):
        result, model = self.run_response()
        payload = json.loads(model.call_args.kwargs["messages"][1]["content"])
        self.assertEqual(payload["numeric_hints"]["numbers_not_found"], ["20"])
        self.assertEqual(result.data.checks[0].verdict, Verdict.SUPPORTED)

    def test_context_isolated_per_claim_and_inputs_not_mutated(self):
        self.bundle.evidence_items.append(EvidenceItem("c2", "doc2", "v2", 2, "다른 문서의 원문", [{}], True))
        self.draft.claims.append(DraftClaim("claim_second", "별도 주장", ["c2"]))
        before = asdict(self.draft), asdict(self.bundle)
        result, model = self.run_response([self.response(), self.response("claim_second", "insufficient")])
        self.assertEqual(result.status, "success")
        for call, reference in zip(model.call_args_list, ("c1", "c2")):
            messages = call.kwargs["messages"]
            self.assertEqual([m["role"] for m in messages], ["system", "user"])
            self.assertEqual([e["chunk_id"] for e in json.loads(messages[1]["content"])["evidence"]], [reference])
        self.assertEqual((asdict(self.draft), asdict(self.bundle)), before)

    def test_network_error_is_retryable_and_does_not_retry_automatically(self):
        for error in (URLError("private URL"), TimeoutError(),
                      HTTPError("secret", 429, "rate", {}, None),
                      HTTPError("secret", 503, "server", {}, None)):
            with self.subTest(error=type(error)):
                result, model = self.run_response([error])
                self.assertTrue(result.diagnostics["verify_claims"].failed_items[0].retryable)
                self.assertNotIn("secret", json.dumps(asdict(result)))
                model.assert_called_once()

    def test_authentication_and_invalid_model_response_are_not_retryable(self):
        for error in (HTTPError("secret", 401, "key", {}, None), ValueError("private response")):
            result, model = self.run_response([error])
            self.assertFalse(result.diagnostics["verify_claims"].failed_items[0].retryable)
            model.assert_called_once()

    def test_bad_context_serialization_is_item_failure_before_model(self):
        self.evidence.source_refs = [{"bbox": [float("nan")]}]
        result, model = self.run_response()
        self.assertEqual(result.status, "error")
        self.assertEqual(result.diagnostics["verify_claims"].failed_items[0].error_code, "INVALID_VERIFICATION_CONTEXT")
        model.assert_not_called()

    def test_invalid_config_never_calls_model(self):
        cases = [("timeout", True), ("timeout", float("nan")), ("timeout", 0),
                 ("model", " "), ("provider", "invalid"),
                 ("base_url", "http://user:secret@localhost"), ("base_url", "http://localhost:bad"),
                 ("base_url", "http://localhost/api/chat")]
        for name, value in cases:
            old = getattr(self.client, name)
            with self.subTest(name=name, value=value), patch.object(self.client, "chat") as model:
                setattr(self.client, name, value)
                result = self.node.run("req", self.draft, self.bundle)
                self.assertEqual(result.status, "error"); model.assert_not_called()
            setattr(self.client, name, old)

    def test_cloud_auth_requires_official_https_endpoint(self):
        self.client.provider = "cloud"; self.client.api_key = "test-key"
        for url in ("http://ollama.com", "https://other.example", "https://ollama.com:444"):
            self.client.base_url = url
            result, model = self.run_response()
            self.assertEqual(result.status, "error"); model.assert_not_called()
        self.client.base_url = "https://ollama.com"
        result, _ = self.run_response()
        self.assertEqual(result.status, "success")
        self.client.api_key = None
        result, model = self.run_response()
        self.assertEqual(result.status, "error"); model.assert_not_called()

    def test_parse_rejects_malformed_wrong_types_and_contract_violations(self):
        bad = [None, "", "   ", "not JSON", "```json\n{}\n```", "[]", "null", '"text"', "{}",
               self.response("wrong"), self.response(verdict="SUPPORTED"), self.response(verdict="maybe"),
               self.response(reason=" "), self.response(reason=None), self.response(verdict=[]),
               '{"claim_id":"claim_0007","verdict":"supported","reason":"a","reason":"b"}',
               json.dumps(dict(claim_id="claim_0007", verdict="supported", reason="ok", evidence_refs=["fake"]))]
        for value in bad:
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                self.node._parse_verification(value, self.claim, [self.evidence])

    def test_parse_requires_actual_linked_evidence_and_preserves_identity(self):
        for evidence in ([], [EvidenceItem("other", "d", "v", 1, "text", [{}], False)]):
            with self.assertRaises(ValueError):
                self.node._parse_verification(self.response(), self.claim, evidence)
        parsed = self.node._parse_verification(self.response(reason="  이유  "), self.claim, [self.evidence])
        self.assertEqual((parsed.claim_id, parsed.reason, parsed.evidence_refs), ("claim_0007", "이유", ["c1"]))

    def test_report_status_table_and_preserves_prior_diagnostics(self):
        check = ClaimVerification("c1", Verdict.CONTRADICTED, "이유", ["source"])
        failure = FailedItem("c2", "TEST", False)
        for checks, failures, count, status in (([], [], 0, "success"), ([check], [], 1, "success"),
                ([check], [failure], 2, "partial"), ([], [failure], 1, "error")):
            diagnostics = {"before": StepDiagnostic("success", count=1)}
            result = self.node._build_report("req", checks=checks, failed_items=failures,
                                             diagnostics=diagnostics, eligible_count=count)
            self.assertEqual(result.status, status)
            self.assertNotIn("verify_claims", diagnostics)
            self.assertIn("before", result.diagnostics)
            self.assertIsNone(result.outcome)
            if status == "error": self.assertIsNone(result.data)
            else: self.assertEqual(result.data.checks, checks)

    def test_report_rejects_missing_duplicate_and_overlapping_counts(self):
        check = ClaimVerification("c1", Verdict.SUPPORTED, "이유", ["source"])
        failure = FailedItem("c1", "TEST", False)
        for checks, failures, count in (([], [], True), ([], [], -1), ([], [], 1),
                ([check, check], [], 2), ([], [failure, failure], 2), ([check], [failure], 2)):
            with self.subTest(count=count), self.assertRaises((TypeError, ValueError)):
                self.node._build_report("req", checks=checks, failed_items=failures,
                                        diagnostics={}, eligible_count=count)

    def test_repeated_runs_do_not_reuse_results(self):
        first, _ = self.run_response()
        second, _ = self.run_response(["bad"])
        self.assertEqual(first.status, "success")
        self.assertEqual(second.status, "error")
        self.assertEqual(len(second.diagnostics["verify_claims"].failed_items), 1)


class GroundingCliTests(GroundingFixture):
    def files(self, root, *, wrapped=True):
        draft, bundle = root / "draft.json", root / "bundle.json"
        draft_data, bundle_data = asdict(self.draft), asdict(self.bundle)
        if wrapped:
            draft_data = dict(status="success", data=draft_data)
            bundle_data = dict(status="partial", data=bundle_data)
        draft.write_text(json.dumps(draft_data, ensure_ascii=False), encoding="utf-8")
        bundle.write_text(json.dumps(bundle_data, ensure_ascii=False), encoding="utf-8")
        return draft, bundle

    def cli(self, draft, bundle, *extra):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--draft", str(draft), "--evidence-bundle", str(bundle), "--model", "test", *extra])
        return code, json.loads(output.getvalue())

    def test_cli_reads_wrapped_and_plain_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for wrapped in (True, False):
                draft, bundle = self.files(Path(directory), wrapped=wrapped)
                with patch.object(OllamaClient, "chat", return_value=self.response()) as model:
                    code, result = self.cli(draft, bundle)
                self.assertEqual((code, result["status"]), (0, "success"))
                self.assertEqual(result["data"]["checks"][0]["evidence_refs"], ["c1"])
                model.assert_called_once()

    def test_cli_error_and_partial_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            self.draft.claims.append(DraftClaim("second", "다른 주장", ["c1"]))
            draft, bundle = self.files(Path(directory))
            with patch.object(OllamaClient, "chat", side_effect=[self.response(), "invalid"]):
                code, result = self.cli(draft, bundle)
            self.assertEqual((code, result["status"]), (0, "partial"))
            with patch.object(OllamaClient, "chat", return_value="invalid"):
                code, result = self.cli(draft, bundle)
            self.assertEqual((code, result["status"]), (2, "error"))

    def test_cli_rejects_file_errors_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            draft, bundle = self.files(Path(directory))
            for content in ('{"status":"error","data":null}', "[]", "bad", '{"claims":[]}'):
                draft.write_text(content)
                with patch.object(OllamaClient, "chat") as model:
                    code, result = self.cli(draft, bundle)
                self.assertEqual((code, result["status"]), (2, "error")); model.assert_not_called()
            with patch.object(OllamaClient, "chat") as model:
                code, result = self.cli(Path(directory)/"missing", bundle)
            self.assertEqual(code, 2); model.assert_not_called()

    def test_cli_request_mismatch_is_not_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle.request_ref = "other"
            draft, bundle = self.files(Path(directory))
            with patch.object(OllamaClient, "chat") as model:
                code, result = self.cli(draft, bundle)
            self.assertEqual(code, 2); model.assert_not_called()

    def test_cli_config_options_and_loader_preserve_claim_id(self):
        with tempfile.TemporaryDirectory() as directory:
            draft, bundle = self.files(Path(directory))
            self.assertEqual(load_review_draft(draft).claims[0].claim_id, self.claim.claim_id)
            with patch.object(OllamaClient, "chat", return_value=self.response()):
                code, result = self.cli(draft, bundle, "--base-url", "http://127.0.0.1:11435", "--timeout", "2")
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
