"""Contract migration regressions, without model/network calls."""
from copy import deepcopy
from dataclasses import asdict
import json
import unittest
from unittest.mock import patch

from rag.contracts import (
    DocumentRef, EvidenceBundle, EvidenceItem, RetrievalRequest, RevisionTarget,
)
from rag.nodes.draft_answer import DraftAnswerNode
from rag.nodes.retrieve_evidence import retrieve_evidence
from rag.retrieval.bm25 import BM25Retriever


class RevisionContractTests(unittest.TestCase):
    def setUp(self):
        self.bundle = EvidenceBundle('request', [EvidenceItem(
            'c1', 'doc', 'v1', 1, '2025년 사업비는 120백만원이다.',
            [dict(block_id='b1', start_offset=0, end_offset=20, source_locator={})], False)], 'test-v1')
        self.target = RevisionTarget('claim_0007', '사업비는 100백만원이다.', '금액 불일치', ['c1'])
        self.node = DraftAnswerNode('mock-model', 'http://localhost:11434')
        self.network = patch('rag.llm.ollama_client.urlopen', side_effect=AssertionError('No live calls'))
        self.network.start(); self.addCleanup(self.network.stop)

    def run_response(self, payload, targets=None, request_ref='request'):
        raw = json.dumps(payload, ensure_ascii=False)
        with patch.object(self.node.client, 'chat', return_value=raw) as model:
            result = self.node.run(request_ref, '2025년 사업비는?', self.bundle,
                                   revision_targets=targets)
        return result, model

    def test_revision_preserves_nonsequential_id_and_inputs(self):
        before = asdict(self.bundle)
        r, model = self.run_response(dict(claims=[dict(claim_id='claim_0007',
            claim_text='사업비는 120백만원이다.', evidence_refs=['c1'])], unanswered_items=[]), [self.target])
        self.assertEqual(r.status, 'success')
        self.assertEqual(r.data.claims[0].claim_id, 'claim_0007')
        self.assertEqual(asdict(self.bundle), before)
        model.assert_called_once()

    def test_revision_forbids_new_id(self):
        r, _ = self.run_response(dict(claims=[dict(claim_id='claim_0001', claim_text='수정',
            evidence_refs=['c1'])], unanswered_items=[]), [self.target])
        self.assertEqual(r.status, 'error')

    def test_revision_forbids_empty_reference(self):
        r, _ = self.run_response(dict(claims=[dict(claim_id='claim_0007', claim_text='수정',
            evidence_refs=[])], unanswered_items=[]), [self.target])
        self.assertEqual(r.status, 'error')

    def test_revision_forbids_unknown_reference(self):
        r, _ = self.run_response(dict(claims=[dict(claim_id='claim_0007', claim_text='수정',
            evidence_refs=['missing'])], unanswered_items=[]), [self.target])
        self.assertEqual(r.status, 'error')

    def test_revision_cannot_add_unanswered_item(self):
        r, _ = self.run_response(dict(claims=[], unanswered_items=['추가']), [self.target])
        self.assertEqual(r.status, 'error')

    def test_uncorrectable_is_empty_success(self):
        r, _ = self.run_response(dict(claims=[], unanswered_items=[]), [self.target])
        self.assertEqual((r.status, r.outcome), ('success', 'insufficient_evidence'))

    def test_empty_revision_targets_skip_model(self):
        r, model = self.run_response({}, [])
        self.assertEqual(r.status, 'success'); model.assert_not_called()

    def test_mismatched_request_skips_model(self):
        r, model = self.run_response({}, request_ref='another')
        self.assertEqual(r.status, 'error'); model.assert_not_called()

    def test_partial_answer_is_not_partial_execution(self):
        r, _ = self.run_response(dict(claims=[dict(claim_text='사업비는 120백만원이다.',
            evidence_refs=['c1'])], unanswered_items=['인건비']))
        self.assertEqual((r.status, r.outcome), ('success', 'partial_answer'))

    def test_some_unreferenced_claims_remain_unverified(self):
        r, _ = self.run_response(dict(claims=[dict(claim_text='사업비는 120백만원이다.', evidence_refs=['c1']),
            dict(claim_text='근거 없는 문장', evidence_refs=[])], unanswered_items=[]))
        self.assertEqual(r.status, 'success')
        self.assertEqual(r.data.claims[1].evidence_refs, [])

    def test_all_unreferenced_is_error(self):
        r, _ = self.run_response(dict(claims=[dict(claim_text='문장', evidence_refs=[])], unanswered_items=[]))
        self.assertEqual(r.status, 'error'); self.assertIsNone(r.data)

    def test_duplicate_revision_targets_skip_model(self):
        r, model = self.run_response({}, [self.target, deepcopy(self.target)])
        self.assertEqual(r.status, 'error'); model.assert_not_called()


class ScopedRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.retriever = BM25Retriever([
            dict(chunk_id='outside', doc_id='other', version_sha256='v1', search_text='사업 사업 예산'),
            dict(chunk_id='inside', doc_id='doc', version_sha256='v1', search_text='사업 계획'),
            dict(chunk_id='other', doc_id='other', version_sha256='v1', search_text='환경 연구'),
        ])

    def run_node(self, query='사업', scope=None):
        scope = [DocumentRef('doc', 'v1')] if scope is None else scope
        return retrieve_evidence(RetrievalRequest('request', query, scope, 'bm25-baseline-v1'),
                                 self.retriever, top_k=1)

    def test_scope_applied_before_final_top_k(self):
        r = self.run_node()
        self.assertEqual([c.chunk_id for c in r.data.candidates], ['inside'])
        self.assertEqual(r.data.candidates[0].rank, 1)

    def test_missing_version_is_error(self):
        self.assertEqual(self.run_node(scope=[DocumentRef('doc', 'v2')]).status, 'error')

    def test_empty_scope_is_success_no_candidates(self):
        r = self.run_node(scope=[])
        self.assertEqual((r.status, r.outcome, r.data.candidates), ('success', 'no_candidates', []))

    def test_no_keyword_match_is_no_candidates(self):
        r = self.run_node(query='zzzxq999')
        self.assertEqual((r.status, r.outcome), ('success', 'no_candidates'))

    def test_runtime_index_reference_is_reproducible(self):
        self.assertEqual(self.run_node().data.index_refs, self.run_node().data.index_refs)
        self.assertTrue(self.run_node().data.index_refs['bm25'].startswith('bm25-runtime-sha256:'))

    def test_search_exception_becomes_node_error(self):
        with patch.object(self.retriever, 'search', side_effect=OSError('private raw detail')):
            r = self.run_node()
        self.assertEqual(r.status, 'error')
        self.assertNotIn('private raw detail', json.dumps(asdict(r)))
