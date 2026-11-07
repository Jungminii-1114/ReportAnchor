"""T-01 public workflow contracts and bounded graph execution; no live model calls."""
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rag.contracts import (
    ClaimVerification, ContextExpansionConfig, DocumentRef, DraftClaim, EvidenceBundle,
    EvidenceItem, FailedItem, NodeResult, RetrievalCandidate, RetrievalCandidates,
    ReviewDraft, RoutedTask, StepDiagnostic, VerificationReport, Verdict,
)
from rag.nodes.draft_answer import DraftAnswerNode
from rag.workflows.document_qa import DocumentQAWorkflow, main


class WorkflowFixture(unittest.TestCase):
    def setUp(self):
        self.task = RoutedTask("request", "  원래 질문: 사업비는?  ", [DocumentRef("doc", "v1")],
                               "bm25-baseline-v1", "fixed-context-v1")
        self.generator = DraftAnswerNode("mock-model", "http://localhost:11434")
        self.workflow = DocumentQAWorkflow(None, {}, {}, self.generator)
        self.bundle = EvidenceBundle("request", [EvidenceItem("source", "doc", "v1", 1,
            "2025년 사업비는 120백만원이다.", [{"block_id": "b1", "source_locator": {"pdf_page": 3}}], False)],
            "fixed-context-v1")
        self.claims = [DraftClaim("c1", "원래 확인된 문장", ["source"]),
                       DraftClaim("c2", "틀린 원문장", ["source"]),
                       DraftClaim("c3", "근거 부족 문장", ["source"])]
        self.draft = ReviewDraft("request", self.claims, [])
        self.retrieval = NodeResult("success", RetrievalCandidates("request", [
            RetrievalCandidate("source", "doc", "v1", 1, {"bm25_score": 1.0})], {}, "test"), {}, None)
        self.network = patch("rag.llm.ollama_client.urlopen", side_effect=AssertionError("No live model calls"))
        self.network.start(); self.addCleanup(self.network.stop)

    def report(self, verdicts, *, status="success", failed=None, claims=None):
        claims = self.claims if claims is None else claims
        checks = [ClaimVerification(claim.claim_id, Verdict(verdict), "판정 사유", list(claim.evidence_refs))
                  for claim, verdict in zip(claims, verdicts) if verdict is not None]
        diagnostics = {}
        if failed:
            diagnostics["verify_claims"] = StepDiagnostic("failed", failed_items=[
                FailedItem(claim_id, "MODEL_TIMEOUT", True) for claim_id in failed])
        return NodeResult(status, VerificationReport("request", checks) if status != "error" else None,
                          diagnostics, None)

    def error(self, code="MODEL_TIMEOUT"):
        return NodeResult("error", None, {"failed_step": StepDiagnostic(
            "failed", error_code=code, retryable=True)}, None)

    def execute(self, reports=None, revisions=None, retrieval=None, context=None, initial=None):
        if reports is None: reports = [self.report(["supported"] * len(self.claims))]
        drafts = [NodeResult("success", self.draft, {}, None) if initial is None else initial]
        if revisions: drafts.extend(revisions)
        with patch("rag.workflows.document_qa.retrieve_evidence", return_value=self.retrieval if retrieval is None else retrieval) as search, \
             patch("rag.workflows.document_qa.get_source_context", return_value=NodeResult("success", self.bundle, {}, None) if context is None else context) as source, \
             patch.object(self.generator, "run", side_effect=drafts) as generation, \
             patch.object(self.workflow.verifier, "run", side_effect=reports) as verification:
            state = self.workflow.run_with_state(self.task)
        return state, search, source, generation, verification


class DocumentQAWorkflowTests(WorkflowFixture):
    def test_all_supported_and_sources_preserved(self):
        state, search, source, generation, verification = self.execute()
        result = state["result"]
        self.assertEqual((result.status, result.outcome, result.error), ("success", None, None))
        self.assertEqual([c.claim_id for c in result.verified_claims], ["c1", "c2", "c3"])
        self.assertEqual(result.verified_claims[0].sources[0].document_version, "v1")
        self.assertEqual(result.verified_claims[0].sources[0].source_refs, self.bundle.evidence_items[0].source_refs)
        self.assertFalse(result.evidence_incomplete)
        for call in (search, source, generation, verification): call.assert_called_once()
        self.assertNotIn("n04_revision", state)
        self.assertEqual(search.call_args.args[0].search_text, self.task.question)
        self.assertEqual(generation.call_args.args[1], self.task.question)
        self.assertIs(self.workflow.verifier.client, self.generator.client)

    def test_only_contradicted_corrected_once_and_only_corrected_reverified(self):
        revised = ReviewDraft("request", [DraftClaim("c2", "고친 문장", ["source"])], [])
        reports = [self.report(["supported", "contradicted", "insufficient"]),
                   self.report(["supported"], claims=revised.claims)]
        state, search, source, generation, verification = self.execute(reports,
            [NodeResult("success", revised, {}, None)])
        result = state["result"]
        self.assertEqual((result.status, result.outcome), ("success", "partial_answer"))
        self.assertEqual([c.claim_text for c in result.verified_claims], ["원래 확인된 문장", "고친 문장"])
        self.assertEqual(result.unconfirmed_items[0].claim_id, "c3")
        self.assertEqual(result.unconfirmed_items[0].claim_text, "근거 부족 문장")
        targets = generation.call_args_list[1].kwargs["revision_targets"]
        self.assertEqual([target.claim_id for target in targets], ["c2"])
        self.assertEqual(verification.call_args_list[1].args[1].claims, revised.claims)
        self.assertIs(generation.call_args_list[0].args[2], generation.call_args_list[1].args[2])
        self.assertEqual(generation.call_count, 2); self.assertEqual(verification.call_count, 2)
        search.assert_called_once(); source.assert_called_once()

    def test_repeated_contradiction_stops_and_hides_false_sentence(self):
        revised = ReviewDraft("request", [DraftClaim("c2", "고친 뒤에도 틀린 문장", ["source"])], [])
        reports = [self.report(["supported", "contradicted", "insufficient"]),
                   self.report(["contradicted"], claims=revised.claims)]
        state, _, _, generation, verification = self.execute(reports, [NodeResult("success", revised, {}, None)])
        result = state["result"]
        self.assertEqual((result.status, result.outcome), ("success", "partial_answer"))
        unconfirmed = next(i for i in result.unconfirmed_items if i.claim_id == "c2")
        self.assertIsNone(unconfirmed.claim_text)
        self.assertNotIn("고친 뒤에도 틀린 문장", json.dumps(asdict(result), ensure_ascii=False))
        self.assertEqual(generation.call_count, 2); self.assertEqual(verification.call_count, 2)

    def test_revised_insufficient_keeps_revised_text_without_third_call(self):
        revised = ReviewDraft("request", [DraftClaim("c2", "보정한 미확인 문장", ["source"])], [])
        reports = [self.report(["supported", "contradicted", "insufficient"]),
                   self.report(["insufficient"], claims=revised.claims)]
        state, _, _, generation, _ = self.execute(reports, [NodeResult("success", revised, {}, None)])
        item = next(i for i in state["result"].unconfirmed_items if i.claim_id == "c2")
        self.assertEqual((item.reason_code, item.claim_text), ("insufficient", "보정한 미확인 문장"))
        self.assertEqual(generation.call_count, 2)

    def test_insufficient_not_corrected_and_unanswered_no_reference_retained(self):
        self.claims[1].evidence_refs = []
        self.draft.unanswered_items = ["시설비 항목"]
        state, _, _, generation, _ = self.execute([self.report(["supported", None, "insufficient"])])
        items = state["result"].unconfirmed_items
        self.assertEqual({item.reason_code for item in items}, {"no_evidence_ref", "unanswered_item", "insufficient"})
        self.assertNotIn("문서에 없음", json.dumps(asdict(state["result"]), ensure_ascii=False))
        generation.assert_called_once()

    def test_empty_search_stops_all_later_nodes(self):
        self.retrieval.data.candidates = []
        state, _, source, generation, verification = self.execute()
        result = state["result"]
        self.assertEqual((result.status, result.outcome), ("success", "insufficient_evidence"))
        source.assert_not_called(); generation.assert_not_called(); verification.assert_not_called()
        self.assertNotIn("n02", state)

    def test_partial_empty_dense_search_is_not_proof_of_absence(self):
        self.retrieval.status = "partial"; self.retrieval.data.candidates = []
        self.retrieval.diagnostics["dense_search"] = StepDiagnostic("failed", error_code="DENSE_FAILED")
        state, _, _, _, _ = self.execute()
        result = state["result"]
        self.assertEqual((result.status, result.outcome, result.evidence_incomplete),
                         ("partial", "insufficient_evidence", True))

    def test_reranker_failure_changes_status_without_evidence_incomplete(self):
        self.retrieval.status = "partial"
        self.retrieval.diagnostics["rerank"] = StepDiagnostic("failed", error_code="RERANK_FAILED")
        state, *_ = self.execute()
        self.assertEqual((state["result"].status, state["result"].outcome, state["result"].evidence_incomplete),
                         ("partial", None, False))

    def test_partial_context_and_budget_exclusions_mark_incomplete(self):
        for status, diagnostics in (("partial", {}), ("success", {"context_budget": StepDiagnostic("success", excluded_refs=["skipped"])})):
            state, *_ = self.execute(context=NodeResult(status, self.bundle, diagnostics, None))
            self.assertTrue(state["result"].evidence_incomplete)
            self.assertEqual(state["result"].status, status)

    def test_empty_valid_context_skips_generation(self):
        self.bundle.evidence_items = []
        state, _, _, generation, verification = self.execute()
        self.assertEqual(state["result"].outcome, "insufficient_evidence")
        generation.assert_not_called(); verification.assert_not_called()

    def test_empty_draft_preserves_unanswered_and_skips_verification(self):
        self.draft.claims = []; self.draft.unanswered_items = ["금액 확인 불가"]
        state, _, _, _, verification = self.execute()
        self.assertEqual(state["result"].outcome, "insufficient_evidence")
        self.assertEqual(state["result"].unconfirmed_items[0].reason_code, "unanswered_item")
        verification.assert_not_called()

    def test_all_insufficient_is_successful_abstention_without_revision(self):
        state, _, _, generation, verification = self.execute([self.report(["insufficient"] * 3)])
        result = state["result"]
        self.assertEqual((result.status, result.outcome, len(result.unconfirmed_items)),
                         ("success", "insufficient_evidence", 3))
        generation.assert_called_once(); verification.assert_called_once()

    def test_numeric_hint_failure_alone_does_not_make_workflow_partial(self):
        report = self.report(["supported"] * 3)
        report.diagnostics["collect_numeric_hints"] = StepDiagnostic("failed", failed_items=[
            FailedItem("c1", "NUMERIC_HINTS_FAILED", False)])
        state, *_ = self.execute([report])
        self.assertEqual(state["result"].status, "success")

    def test_missing_recheck_for_one_correction_does_not_preserve_old_verdict(self):
        revisions = ReviewDraft("request", [DraftClaim("c2", "수정2", ["source"]),
                                            DraftClaim("c3", "수정3", ["source"])], [])
        state, *_ = self.execute([self.report(["supported", "contradicted", "contradicted"]),
            self.report(["supported", None], claims=revisions.claims, status="partial", failed=["c3"])],
            [NodeResult("success", revisions, {}, None)])
        result = state["result"]
        self.assertEqual([c.claim_id for c in result.verified_claims], ["c1", "c2"])
        self.assertEqual(result.unconfirmed_items[0].reason_code, "verification_failed")
        self.assertEqual(result.unconfirmed_items[0].claim_text, "수정3")

    def test_required_node_errors_have_location_and_no_answer(self):
        for name, kwargs in (("N-01", {"retrieval":self.error()}), ("N-02", {"context":self.error()}),
                ("N-04", {"initial":self.error()}), ("N-03", {"reports":[self.error()]})):
            with self.subTest(node=name):
                state, *_ = self.execute(**kwargs)
                result = state["result"]
                self.assertEqual((result.status, result.outcome), ("error", None))
                self.assertEqual(result.error.node, name)
                self.assertTrue(result.error.retryable)
                self.assertEqual(result.verified_claims, [])

    def test_revision_call_error_preserves_supported_and_hides_contradicted(self):
        state, _, _, generation, _ = self.execute([self.report(["supported", "contradicted", "insufficient"])], [self.error()])
        result = state["result"]
        self.assertEqual((result.status, result.outcome, result.error), ("partial", "partial_answer", None))
        self.assertEqual([c.claim_id for c in result.verified_claims], ["c1"])
        item = next(i for i in result.unconfirmed_items if i.claim_id == "c2")
        self.assertEqual((item.claim_text, item.reason_code), (None, "revision_failed"))
        self.assertEqual(generation.call_count, 2)

    def test_revision_error_without_confirmed_claim_is_error(self):
        state, *_ = self.execute([self.report(["insufficient", "contradicted", "insufficient"])], [self.error()])
        self.assertEqual((state["result"].status, state["result"].error.node), ("error", "N-04"))

    def test_uncorrectable_claim_is_normal_insufficient_not_execution_error(self):
        state, _, _, generation, verification = self.execute([self.report(["insufficient", "contradicted", "insufficient"])],
            [NodeResult("success", ReviewDraft("request", [], []), {}, "insufficient_evidence")])
        result = state["result"]
        self.assertEqual((result.status, result.outcome), ("success", "insufficient_evidence"))
        self.assertEqual(generation.call_count, 2); verification.assert_called_once()
        self.assertIsNone(next(i for i in result.unconfirmed_items if i.claim_id == "c2").claim_text)

    def test_recheck_error_does_not_reuse_original_supported_status(self):
        revised = ReviewDraft("request", [DraftClaim("c2", "아직 검증되지 않은 수정문", ["source"])], [])
        state, _, _, _, _ = self.execute([self.report(["supported", "contradicted", "insufficient"]), self.error()],
            [NodeResult("success", revised, {}, None)])
        result = state["result"]
        self.assertEqual((result.status, result.outcome), ("partial", "partial_answer"))
        self.assertEqual([c.claim_id for c in result.verified_claims], ["c1"])
        item = next(i for i in result.unconfirmed_items if i.claim_id == "c2")
        self.assertEqual((item.reason_code, item.claim_text), ("verification_failed", "아직 검증되지 않은 수정문"))

    def test_partial_verification_keeps_completed_and_failed_identity(self):
        state, *_ = self.execute([self.report(["supported", None, "insufficient"], status="partial", failed=["c2"])])
        result = state["result"]
        self.assertEqual(result.status, "partial")
        self.assertFalse(result.evidence_incomplete)
        self.assertEqual(next(i for i in result.unconfirmed_items if i.claim_id == "c2").reason_code, "verification_failed")

    def test_bad_routed_task_stops_before_search(self):
        bad_tasks = [None, {}, RoutedTask("request", " ", [], "bm25-baseline-v1", "fixed-context-v1"),
            RoutedTask("request", "질문", [], "hyde", "fixed-context-v1"),
            RoutedTask("request", "질문", [], "bm25-baseline-v1", "unknown")]
        for task in bad_tasks:
            with patch("rag.workflows.document_qa.retrieve_evidence") as search:
                result = self.workflow.run(task)
            self.assertEqual(result.error.error_code, "INVALID_ROUTED_TASK"); search.assert_not_called()

    def test_wrong_request_and_out_of_scope_evidence_fail_closed(self):
        self.bundle.request_ref = "other"
        state, *_ = self.execute()
        self.assertEqual(state["result"].error.error_code, "NODE_CONTRACT_VIOLATION")
        self.bundle.request_ref = "request"; self.bundle.evidence_items[0].document_version = "wrong"
        state, *_ = self.execute()
        self.assertEqual(state["result"].error.node, "N-02")

    def test_revision_cannot_add_new_claim_or_overwrite_supported(self):
        for claim_id in ("new_id", "c1"):
            revised = ReviewDraft("request", [DraftClaim(claim_id, "불필요한 수정", ["source"])], [])
            state, *_ = self.execute([self.report(["supported", "contradicted", "insufficient"])],
                                    [NodeResult("success", revised, {}, None)])
            self.assertEqual(state["result"].status, "partial")
            self.assertEqual(state["result"].verified_claims[0].claim_text, "원래 확인된 문장")

    def test_incomplete_or_unknown_verification_checks_fail_closed(self):
        for report in (self.report(["supported"]), self.report(["supported"] * 3)):
            if len(report.data.checks) == 3: report.data.checks[1].claim_id = "unknown"
            state, *_ = self.execute([report])
            self.assertEqual(state["result"].status, "error")
            self.assertEqual(state["result"].error.error_code, "NODE_CONTRACT_VIOLATION")

    def test_graph_contains_only_four_small_nodes_and_state_helpers(self):
        names = set(self.workflow.graph.get_graph().nodes)
        self.assertTrue({"N-01", "N-02", "N-03", "N-04", "validate_input", "assemble_result"} <= names)
        self.assertFalse(any("revision" in name for name in names))

    def test_runs_do_not_share_state_or_mutate_input(self):
        before = asdict(self.task), asdict(self.bundle), asdict(self.draft)
        first, *_ = self.execute()
        self.retrieval.data.candidates = []
        second, *_ = self.execute()
        self.assertEqual(len(first["result"].verified_claims), 3)
        self.assertEqual(second["result"].verified_claims, [])
        self.assertNotIn("n03", second)
        self.assertEqual((asdict(self.task), asdict(self.bundle), asdict(self.draft)), before)


class RealNodeAndCliTests(unittest.TestCase):
    def setUp(self):
        from rag.nodes.get_source_context import build_chunk_lookup
        from rag.retrieval.bm25 import BM25Retriever
        # Minimal compatible native-text chunk and original block fixture.
        self.text = "2025년 사업비는 120백만원이다."
        locator = dict(type="pdf_page_bbox", pdf_page=1, bbox=[0, 0, 100, 20],
                       units="pt", coordinate_space="pymupdf_unrotated_page")
        self.block = dict(block_id="b1", doc_id="doc", version_sha256="v1", pdf_page=1,
                          text=self.text, block_index=0, source_locator=locator)
        self.chunk = dict(chunk_id="source", doc_id="doc", version_sha256="v1", text=self.text,
            search_text=self.text, pdf_pages=[1], source_spans=[dict(block_id="b1", pdf_page=1,
                start_char=0, end_char=len(self.text), source_locator=deepcopy(locator))])
        self.generator = DraftAnswerNode("mock-model", "http://localhost:11434")
        self.workflow = DocumentQAWorkflow(BM25Retriever([self.chunk]), build_chunk_lookup([self.chunk]),
            {"b1":self.block}, self.generator)
        self.task = RoutedTask("request", "2025년 사업비는?", [DocumentRef("doc", "v1")],
                               "bm25-baseline-v1", "fixed-context-v1")
        self.guard = patch("rag.llm.ollama_client.urlopen", side_effect=AssertionError("No live model calls"))
        self.guard.start(); self.addCleanup(self.guard.stop)

    def mock_model(self, *, messages, **_):
        payload = json.loads(messages[-1]["content"])
        if "claim" in payload:
            return json.dumps(dict(claim_id=payload["claim"]["claim_id"], verdict="supported", reason="모의 판정"))
        return json.dumps(dict(claims=[dict(claim_text="사업비는 120백만원이다.", evidence_refs=["source"])], unanswered_items=[]))

    def test_real_small_nodes_link_with_mocked_transport(self):
        with patch.object(self.generator.client, "chat", side_effect=self.mock_model) as model:
            state = self.workflow.run_with_state(self.task)
        self.assertEqual(state["result"].status, "success")
        self.assertEqual(len(state["result"].verified_claims), 1)
        self.assertEqual(model.call_count, 2)

    def test_real_small_nodes_execute_full_correction_loop(self):
        def responses(*, messages, **_):
            payload = json.loads(messages[-1]["content"])
            if "claim" in payload:
                claim = payload["claim"]
                verdict = "contradicted" if claim["claim_text"] == "사업비는 999백만원이다." else "supported"
                return json.dumps(dict(claim_id=claim["claim_id"], verdict=verdict, reason="모의 판정"))
            if "revision_targets" in payload:
                target = payload["revision_targets"][0]
                return json.dumps(dict(claims=[dict(claim_id=target["claim_id"],
                    claim_text="사업비는 120백만원이다.", evidence_refs=["source"])], unanswered_items=[]))
            return json.dumps(dict(claims=[dict(claim_text="사업비 자료가 있다.", evidence_refs=["source"]),
                dict(claim_text="사업비는 999백만원이다.", evidence_refs=["source"])], unanswered_items=[]))
        with patch.object(self.generator.client, "chat", side_effect=responses) as model:
            state = self.workflow.run_with_state(self.task)
        result = state["result"]
        self.assertEqual((result.status, result.outcome), ("success", None))
        self.assertEqual([c.claim_id for c in result.verified_claims], ["claim_0001", "claim_0002"])
        self.assertEqual(result.verified_claims[1].claim_text, "사업비는 120백만원이다.")
        self.assertNotIn("999", json.dumps(asdict(result), ensure_ascii=False))
        self.assertEqual(model.call_count, 5)

    def test_cli_scope_and_result_json(self):
        from rag.llm.ollama_client import OllamaClient
        with tempfile.TemporaryDirectory() as directory:
            chunks, blocks = Path(directory)/"chunks.jsonl", Path(directory)/"blocks.jsonl"
            chunks.write_text(json.dumps(self.chunk, ensure_ascii=False)+"\n")
            blocks.write_text(json.dumps(self.block, ensure_ascii=False)+"\n")
            stdout = io.StringIO()
            with patch.object(OllamaClient, "chat", side_effect=self.mock_model), redirect_stdout(stdout):
                code = main(["--chunks",str(chunks),"--blocks",str(blocks),"--query",self.task.question,
                             "--request-ref","request","--scope","doc","v1","--model","mock-model"])
            result = json.loads(stdout.getvalue())
            self.assertEqual((code, result["status"]), (0, "success"))
            self.assertEqual(result["verified_claims"][0]["sources"][0]["document_version"], "v1")

    def test_cli_bad_file_returns_error_json_without_model(self):
        from rag.llm.ollama_client import OllamaClient
        stdout = io.StringIO()
        with patch.object(OllamaClient, "chat") as model, redirect_stdout(stdout):
            code = main(["--chunks","/missing/chunks","--blocks","/missing/blocks","--query","질문",
                         "--request-ref","request","--scope","doc","v1","--model","mock-model"])
        self.assertEqual((code,json.loads(stdout.getvalue())["status"]), (2,"error"))
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
