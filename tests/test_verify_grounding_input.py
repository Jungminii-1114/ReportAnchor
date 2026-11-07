"""N-03의 입력 계약과 후속 조회·의미 검증의 경계를 검사한다."""
from copy import deepcopy
from dataclasses import asdict
import unittest
from unittest.mock import Mock

from rag.contracts import DraftClaim, EvidenceBundle, EvidenceItem, ReviewDraft
from rag.llm.ollama_client import OllamaClient
from rag.nodes.verify_grounding import VerifyGroundingNode


class GroundingInputTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(spec=OllamaClient)
        self.node = VerifyGroundingNode(self.client)
        self.draft = ReviewDraft("req", [DraftClaim("claim_7", "사업비는 120백만원이다.", ["c1"])], [])
        self.bundle = EvidenceBundle("req", [EvidenceItem(
            "c1", "doc", "v1", 1, "사업비 120백만원",
            [{"block_id": "b1", "start_offset": 0, "end_offset": 11, "source_locator": {}}], False,
        )], "fixed-test-v1")

    def tearDown(self):
        self.client.chat.assert_not_called()

    def validate(self, request_ref="req", draft=None, bundle=None):
        return self.node._validate_input(request_ref, self.draft if draft is None else draft,
                                         self.bundle if bundle is None else bundle)

    def test_valid_input_returns_none_and_does_not_mutate(self):
        before = asdict(self.draft), asdict(self.bundle)
        self.assertIsNone(self.validate())
        self.assertEqual((asdict(self.draft), asdict(self.bundle)), before)

    def test_top_level_wrong_types(self):
        for request_ref, draft, bundle in [(None, self.draft, self.bundle),
                (123, self.draft, self.bundle), ("req", None, self.bundle),
                ("req", {}, self.bundle), ("req", self.draft, None),
                ("req", self.draft, {})]:
            with self.subTest(request_ref=request_ref, draft_type=type(draft), bundle_type=type(bundle)):
                with self.assertRaises(TypeError):
                    self.node._validate_input(request_ref, draft, bundle)

    def test_every_request_mismatch_is_rejected(self):
        # Includes a=b!=c and a!=b=c, which chained != fails to detect.
        for a, b, c in [("req", "req", "other"), ("other", "req", "req"),
                        ("req", "other", "req"), ("a", "b", "c")]:
            with self.subTest(ids=(a, b, c)):
                draft, bundle = deepcopy(self.draft), deepcopy(self.bundle)
                draft.request_ref, bundle.request_ref = b, c
                with self.assertRaises(ValueError):
                    self.node._validate_input(a, draft, bundle)

    def test_empty_and_blank_request_ids(self):
        for value in ["", " ", "\t\n"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(request_ref=value)

    def test_wrong_nested_request_ids(self):
        for target in [self.draft, self.bundle]:
            for value, error in [(None, TypeError), (7, TypeError), (" ", ValueError)]:
                before = target.request_ref
                with self.subTest(target=type(target), value=value):
                    target.request_ref = value
                    try:
                        with self.assertRaises(error):
                            self.validate()
                    finally:
                        target.request_ref = before

    def test_empty_lists_are_valid_input(self):
        self.draft.claims = []
        self.bundle.evidence_items = []
        self.assertIsNone(self.validate())

    def test_unreferenced_claim_is_valid_input(self):
        self.draft.claims[0].evidence_refs = []
        self.assertIsNone(self.validate())

    def test_unknown_reference_is_deferred_to_claim_lookup(self):
        self.draft.claims[0].evidence_refs = ["missing"]
        self.assertIsNone(self.validate())

    def test_wrong_collection_types(self):
        for target, attribute in [(self.draft, "claims"), (self.draft, "unanswered_items"),
                                  (self.bundle, "evidence_items")]:
            before = getattr(target, attribute)
            for value in [None, "not a list", {}]:
                with self.subTest(attribute=attribute, value=value):
                    setattr(target, attribute, value)
                    try:
                        with self.assertRaises(TypeError):
                            self.validate()
                    finally:
                        setattr(target, attribute, before)

    def test_invalid_claim_values(self):
        for attribute, value, error in [("claim_id", " ", ValueError), ("claim_id", [], TypeError),
                ("claim_text", "", ValueError), ("claim_text", None, TypeError),
                ("evidence_refs", "c1", TypeError), ("evidence_refs", [""], ValueError),
                ("evidence_refs", [123], TypeError)]:
            draft = deepcopy(self.draft)
            setattr(draft.claims[0], attribute, value)
            with self.subTest(attribute=attribute, value=value), self.assertRaises(error):
                self.validate(draft=draft)

    def test_nested_objects_must_be_contract_types(self):
        self.draft.claims = [{}]
        with self.assertRaises(TypeError):
            self.validate()
        self.draft.claims = []
        self.bundle.evidence_items = [{}]
        with self.assertRaises(TypeError):
            self.validate()

    def test_duplicate_claim_ids(self):
        self.draft.claims.append(deepcopy(self.draft.claims[0]))
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_chunk_ids(self):
        self.bundle.evidence_items.append(deepcopy(self.bundle.evidence_items[0]))
        with self.assertRaises(ValueError):
            self.validate()

    def test_invalid_evidence_values(self):
        cases = [("chunk_id", "", ValueError), ("chunk_id", [], TypeError),
                 ("document_id", " ", ValueError), ("document_version", None, TypeError),
                 ("context_text", "\n", ValueError), ("retrieval_rank", True, TypeError),
                 ("retrieval_rank", 1.0, TypeError), ("retrieval_rank", 0, ValueError),
                 ("retrieval_rank", -1, ValueError), ("context_truncated", 1, TypeError),
                 ("source_refs", None, TypeError), ("source_refs", [], ValueError),
                 ("source_refs", [None], TypeError)]
        for attribute, value, error in cases:
            bundle = deepcopy(self.bundle)
            setattr(bundle.evidence_items[0], attribute, value)
            with self.subTest(attribute=attribute, value=value), self.assertRaises(error):
                self.validate(bundle=bundle)

    def test_profile_and_unanswered_item_values(self):
        self.bundle.context_profile_ref = " "
        with self.assertRaises(ValueError):
            self.validate()
        self.bundle.context_profile_ref = "fixed-test-v1"
        for item, error in [(None, TypeError), (" ", ValueError)]:
            self.draft.unanswered_items = [item]
            with self.subTest(item=item), self.assertRaises(error):
                self.validate()

    def test_multiple_documents_and_versions_are_allowed(self):
        for chunk_id, doc, version in [("c2", "another_doc", "v1"), ("c3", "doc", "v2")]:
            item = deepcopy(self.bundle.evidence_items[0])
            item.chunk_id, item.document_id, item.document_version = chunk_id, doc, version
            self.bundle.evidence_items.append(item)
        self.assertIsNone(self.validate())

    def test_truncated_context_is_not_validation_failure(self):
        self.bundle.evidence_items[0].context_truncated = True
        self.assertIsNone(self.validate())

    def test_validation_does_not_rewrite_ids_or_judge_claim_truth(self):
        self.draft.claims[0].claim_text = "원문과 다른 주장이지만 의미 검사는 후속 단계다."
        self.draft.claims[0].evidence_refs = ["c1", "c1"]
        self.assertIsNone(self.validate())
        self.assertEqual(self.draft.claims[0].evidence_refs, ["c1", "c1"])


if __name__ == "__main__":
    unittest.main()
