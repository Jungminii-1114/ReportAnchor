"""기본 계약 검증과 중첩 JSON 직렬화 테스트."""
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("REPORT_AGENT_ROOT", str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(ROOT))
candidate = os.environ.get("CONTRACTS_MODULE_PATH")
if candidate:
    spec = importlib.util.spec_from_file_location("rag.contracts", candidate)
    contracts = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contracts
    spec.loader.exec_module(contracts)
else:
    from rag import contracts


class ContractTests(unittest.TestCase):
    def test_document_ref_rejects_bad_ids_and_versions(self):
        for field in ["document_id", "document_version"]:
            for value in [None, "", "  "]:
                args = {"document_id": "doc", "document_version": "version"}
                args[field] = value
                with self.subTest(field=field, value=value), self.assertRaises((TypeError, ValueError)):
                    contracts.DocumentRef(**args)

    def test_candidate_checks_all_ids_and_rank(self):
        good = dict(chunk_id="chunk", document_id="doc", document_version="version",
                    rank=1, score_metadata={"bm25_score": 0.0})
        contracts.RetrievalCandidate(**good)
        for field in ["chunk_id", "document_id", "document_version"]:
            for value in [None, "", "  "]:
                with self.subTest(field=field, value=value), self.assertRaises((TypeError, ValueError)):
                    contracts.RetrievalCandidate(**{**good, field: value})
        for value in [0, -1, True, 1.5, "1"]:
            with self.subTest(rank=value), self.assertRaises((TypeError, ValueError)):
                contracts.RetrievalCandidate(**{**good, "rank": value})

    def test_step_status_values_and_optional_fields(self):
        for status in ["success", "failed", "skipped"]:
            contracts.StepDiagnostic(status=status)
        for value in ["error", "partial", "sucess", None]:
            with self.subTest(status=value), self.assertRaises((TypeError, ValueError)):
                contracts.StepDiagnostic(status=value)
        for values in [{"error_code": 7}, {"reason": []}, {"retryable": 1}]:
            with self.subTest(values=values), self.assertRaises(TypeError):
                contracts.StepDiagnostic(status="failed", **values)

    def test_count_accepts_zero_but_not_negative_bool_or_float(self):
        for value in [None, 0, 3]:
            contracts.StepDiagnostic(status="success", count=value)
        for value in [-1, True, 1.5, "1"]:
            with self.subTest(count=value), self.assertRaises((TypeError, ValueError)):
                contracts.StepDiagnostic(status="success", count=value)

    def test_node_status_and_diagnostics_structure(self):
        for status in ["success", "partial", "error"]:
            contracts.NodeResult(status=status, data=None, diagnostics={}, outcome=None)
        for status in ["failed", "skipped", "no_candidates", None]:
            with self.subTest(status=status), self.assertRaises((TypeError, ValueError)):
                contracts.NodeResult(status=status, data=None, diagnostics={}, outcome=None)
        for diagnostics in [[], {"": contracts.StepDiagnostic("success")},
                            {"bm25_search": {"status": "success"}}]:
            with self.subTest(diagnostics=diagnostics), self.assertRaises((TypeError, ValueError)):
                contracts.NodeResult(status="success", data=None, diagnostics=diagnostics, outcome=None)

    def test_nested_result_serializes_all_four_keys(self):
        hit = contracts.RetrievalCandidate("chunk", "doc", "version", 1, {"bm25_score": 0.0})
        data = contracts.RetrievalCandidates("req", [hit], {"bm25": "test-index"}, "test-strategy")
        result = contracts.NodeResult(status="success", data=data,
                                      diagnostics={"bm25_search": contracts.StepDiagnostic("success", count=1)},
                                      outcome=None)
        payload = json.loads(json.dumps(asdict(result)))
        self.assertEqual(set(payload), {"status", "data", "diagnostics", "outcome"})
        self.assertEqual(payload["data"]["candidates"][0]["chunk_id"], "chunk")
        self.assertIsNone(payload["diagnostics"]["bm25_search"]["reason"])

    def test_empty_partial_data_can_be_preserved(self):
        data = contracts.RetrievalCandidates("req", [], {"bm25": "test-index"}, "test-strategy")
        result = contracts.NodeResult(status="partial", data=data, diagnostics={
            "dense_search": contracts.StepDiagnostic("failed", error_code="TIMEOUT"),
            "bm25_search": contracts.StepDiagnostic("success", count=0),
        }, outcome=None)
        self.assertEqual(asdict(result)["data"]["candidates"], [])


if __name__ == "__main__":
    unittest.main()
