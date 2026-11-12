"""N-05/N-06 계약·논리·실패 검사. 실제 모델과 네트워크는 호출하지 않는다."""
from copy import deepcopy
from dataclasses import asdict
from itertools import product
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from rag.contracts import (
    Criterion, CriterionCondition, CriterionVerdict, EvidenceBundle, EvidenceItem,
)
from rag.llm.ollama_client import OllamaClient
from rag.nodes._criteria_support import aggregate
from rag.nodes.structure_criteria import StructureCriteriaNode
from rag.nodes.evaluate_criterion import EvaluateCriterionNode, claim_id_for, grounding_draft_claim, quote_options


def structured(index=0, original="사업비 기재", operator="all", conditions=None):
    return dict(input_index=index, status="structured", operator=operator,
        conditions=conditions or [dict(text=original, source_quote=original)], reason=None)


def unprocessed(index=0, code="needs_clarification"):
    return dict(input_index=index, status=code, operator=None, conditions=[], reason="조건 명확화 필요")


class StructureCriteriaTests(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient("mock-model", "http://localhost:11434")
        self.node = StructureCriteriaNode(self.client)
        self.network = patch("rag.llm.ollama_client.urlopen", side_effect=AssertionError("실제 호출 금지"))
        self.network.start(); self.addCleanup(self.network.stop)

    def execute(self, texts=None, rows=None, raw=None):
        texts = ["사업비 기재"] if texts is None else texts
        raw = json.dumps({"items": [structured()] if rows is None else rows}, ensure_ascii=False) if raw is None else raw
        with patch.object(self.client, "chat", return_value=raw) as model:
            result = self.node.run("req", texts)
        return result, model

    def test_single_criterion_preserves_raw_text_and_code_assigned_ids(self):
        original = "  2025년 사업비 120백만원 이상\n"
        result, model = self.execute([original], [structured(original=original)])
        self.assertEqual((result.status, result.outcome), ("success", None))
        criterion = result.data.criteria[0]
        self.assertEqual(criterion.original_text, original)
        self.assertEqual(criterion.criterion_id, "criterion_001")
        self.assertEqual(criterion.conditions[0].condition_id, "criterion_001:condition_01")
        self.assertEqual(result.data.request_ref, "req")
        model.assert_called_once()
        args = model.call_args.kwargs
        self.assertEqual([m["role"] for m in args["messages"]], ["system", "user"])
        self.assertEqual(json.loads(args["messages"][1]["content"])["criteria"][0]["original_text"], original)

    def test_compound_all_and_any_preserve_source_quotes(self):
        original = "매출액과 영업이익이 모두 기재되어야 한다."
        conditions = [dict(text="매출액 기재 여부", source_quote="매출액"),
                      dict(text="영업이익 기재 여부", source_quote="영업이익")]
        for op in ("all", "any"):
            result, _ = self.execute([original], [structured(original=original, operator=op, conditions=conditions)])
            self.assertEqual(result.data.criteria[0].operator, op)
            self.assertEqual(len(result.data.criteria[0].conditions), 2)

    def test_reordered_model_output_restores_original_input_order(self):
        result, _ = self.execute(["사업비 기재", "인건비 기재"], [structured(1, "인건비 기재"), structured()])
        self.assertEqual([c.input_index for c in result.data.criteria], [0, 1])

    def test_partial_business_processing_is_success_not_partial_execution(self):
        result, _ = self.execute(["사업비 기재", "A AND (B OR C)"], [structured(), unprocessed(1,"unsupported_structure")])
        self.assertEqual((result.status, result.outcome), ("success", "partial_criteria"))
        self.assertEqual(result.data.unprocessed_criteria[0].original_text, "A AND (B OR C)")

    def test_all_unsupported_and_mixed_no_usable_follow_precedence(self):
        for codes, outcome in [(["unsupported_structure"]*2,"unsupported_structure"),
                               (["unsupported_structure","needs_clarification"],"needs_clarification"),
                               (["needs_clarification"]*2,"needs_clarification")]:
            result, _ = self.execute(["조건 하나", "조건 둘"], [unprocessed(i,c) for i,c in enumerate(codes)])
            self.assertEqual((result.status,result.outcome,len(result.data.criteria)),("success",outcome,0))
            self.assertEqual(len(result.data.unprocessed_criteria),2)

    def test_explicit_nested_logic_cannot_be_silently_flattened(self):
        original="A AND (B OR C)"
        result, _ = self.execute([original],[structured(original=original)])
        self.assertEqual((result.status,result.outcome),("success","unsupported_structure"))
        self.assertFalse(result.data.criteria)

    def test_empty_criteria_skips_model_and_requests_clarification(self):
        result, model = self.execute([],[])
        self.assertEqual((result.status,result.outcome),("success","needs_clarification"))
        model.assert_not_called()

    def test_invalid_inputs_rejected_before_call(self):
        for value in (None,"사업비",[" "],[None],["x"]*21,["x"*4001]):
            with self.subTest(value=type(value)), patch.object(self.client,"chat") as model:
                self.assertEqual(self.node.run("req",value).status,"error")
                model.assert_not_called()
        with patch.object(self.client,"chat") as model:
            self.assertEqual(self.node.run(" ",["조건"]).status,"error");model.assert_not_called()

    def test_omitted_duplicate_out_of_range_and_boolean_indices_fail_closed(self):
        for rows in ([],[structured(),structured()], [structured(index=1)], [structured(index=True)]):
            result, _ = self.execute(rows=rows)
            self.assertEqual((result.status,result.data,result.outcome),("error",None,None))

    def test_malformed_schema_never_becomes_business_unsupported(self):
        for modify in (lambda r:r.update(operator="nested"),lambda r:r.update(conditions=[]),
                       lambda r:r.update(extra=True), lambda r:r.update(status="unknown"),
                       lambda r:r.update(reason="not-null"),lambda r:r["conditions"][0].update(children=[]),
                       lambda r:r["conditions"][0].update(source_quote="invented")):
            row=structured();modify(row)
            result,_=self.execute(rows=[row]);self.assertEqual((result.status,result.outcome),("error",None))

    def test_numeric_thresholds_cannot_be_added_or_omitted(self):
        for original, rewritten in (("사업비 120백만원 이상","사업비 이상"), ("사업비 기재","사업비 120백만원 이상")):
            row=structured(original=original);row["conditions"][0]["text"]=rewritten
            result,_=self.execute([original],[row]);self.assertEqual(result.status,"error")

    def test_thousands_separator_normalization_does_not_change_threshold(self):
        original = "사업비 4,000백만원 이상"
        row = structured(original=original)
        row["conditions"][0]["text"] = "사업비 4000백만원 이상"
        result, _ = self.execute([original], [row])
        self.assertEqual(result.status, "success")
        self.assertEqual(result.data.criteria[0].original_text, original)

    def test_invalid_json_duplicate_keys_and_prose_fail(self):
        for raw in ('{"items":[],"items":[]}', '일반 설명', '```json\n{}\n```','{"items":NaN}'):
            result,_=self.execute(raw=raw);self.assertEqual(result.status,"error")

    def test_model_execution_errors_are_distinct_from_unsupported(self):
        for exc in (TimeoutError(),URLError("private"),HTTPError("secret",503,"error",{},None)):
            with patch.object(self.client,"chat",side_effect=exc): result=self.node.run("req",["사업비 기재"])
            self.assertEqual((result.status,result.outcome),("error",None))
            self.assertTrue(next(iter(result.diagnostics.values())).retryable)
            self.assertNotIn("private",json.dumps(asdict(result)))

    def test_model_config_validation_happens_before_network(self):
        self.client.timeout=0
        with patch.object(self.client,"chat") as model:
            result=self.node.run("req",["기준"])
        self.assertEqual(result.status,"error");model.assert_not_called()


class EvaluateCriterionTests(unittest.TestCase):
    def setUp(self):
        self.client=OllamaClient("mock","http://localhost:11434")
        self.node=EvaluateCriterionNode(self.client)
        self.criterion=Criterion("criterion_001",0,"2025년 사업비는 100백만원 이상이어야 한다.","all",[
            CriterionCondition("condition_001","2025년 사업비는 100백만원 이상","사업비")])
        self.bundle=EvidenceBundle("req",[EvidenceItem("source","doc","v1",1,"2025년 사업비는 120백만원이다.",[
            {"block_id":"b1","source_locator":{"pdf_page":3,"bbox":[0,0,10,10]}}],False)],"fixed-context-v1")
        self.network=patch("rag.llm.ollama_client.urlopen",side_effect=AssertionError("실제 호출 금지"))
        self.network.start();self.addCleanup(self.network.stop)

    def row(self,verdict="met"):
        return dict(condition_id="condition_001",verdict=verdict,claim_text="사업비는 120백만원이다.",
                    reason="직접 근거 확인",quote_ids=["quote_001_001"])

    def execute(self,rows=None):
        with patch.object(self.client,"chat",return_value=json.dumps(dict(conditions=[self.row()] if rows is None else rows))) as model:
            result=self.node.run("req",self.criterion,self.bundle)
        return result,model

    def test_supported_proposal_has_code_assigned_claim_and_exact_quote(self):
        result,model=self.execute()
        self.assertEqual((result.status,result.data.verdict),("success",CriterionVerdict.MET))
        f=result.data.conditions[0]
        self.assertEqual(f.claim_id,claim_id_for(self.criterion,self.criterion.conditions[0]))
        self.assertEqual(f.quotes[0].text,self.bundle.evidence_items[0].context_text)
        model.assert_called_once()

    def test_negative_business_finding_is_not_execution_error(self):
        result,_=self.execute([self.row("not_met")])
        self.assertEqual((result.status,result.data.verdict),("success",CriterionVerdict.NOT_MET))

    def test_empty_evidence_skips_model_without_claiming_absence(self):
        self.bundle.evidence_items=[]
        with patch.object(self.client,"chat") as model:result=self.node.run("req",self.criterion,self.bundle)
        self.assertEqual((result.status,result.outcome),("success","insufficient_evidence"))
        self.assertEqual(result.data.verdict,CriterionVerdict.UNDETERMINED);model.assert_not_called()

    def test_null_evidence_and_request_version_contract_errors_block_calls(self):
        for bundle in (None,EvidenceBundle("other",[],"fixed-context-v1")):
            with patch.object(self.client,"chat") as model:
                self.assertEqual(self.node.run("req",self.criterion,bundle).status,"error");model.assert_not_called()

    def test_decided_findings_require_cited_direct_text(self):
        for verdict in ("met","not_met"):
            row=self.row(verdict);row.update(quote_ids=[])
            result,_=self.execute([row]);self.assertEqual(result.status,"error")

    def test_unknown_with_no_refs_is_valid_and_does_not_fake_negative(self):
        row=self.row("undetermined");row.update(quote_ids=[])
        result,_=self.execute([row]);self.assertEqual(result.data.verdict,CriterionVerdict.UNDETERMINED)

    def test_foreign_refs_wrong_quotes_unquoted_refs_and_ids_are_rejected(self):
        changes=[lambda r:r.update(quote_ids=["foreign"]),lambda r:r.update(quotes=[dict(text="999백만원")]),
                 lambda r:r.update(quote_ids=[]),lambda r:r.update(condition_id="other"),
                 lambda r:r.update(claim_id="invented"),lambda r:r.update(quote_ids=["quote_001_001","quote_001_001"])]
        for change in changes:
            row=self.row();change(row);result,_=self.execute([row]);self.assertEqual(result.status,"error")

    def test_omitted_and_duplicate_conditions_are_rejected(self):
        for rows in ([],[self.row(),self.row()]):
            result,_=self.execute(rows);self.assertEqual(result.status,"error")

    def test_prompt_isolates_metadata_and_preserves_original_inputs(self):
        before=asdict(self.criterion),asdict(self.bundle)
        _,model=self.execute();messages=model.call_args.kwargs["messages"]
        self.assertEqual([m["role"] for m in messages],["system","user"])
        payload=json.loads(messages[-1]["content"])
        self.assertNotIn("source_refs",payload["evidence"][0])
        self.assertEqual("".join(p["text"] for p in payload["evidence"][0]["passages"]),self.bundle.evidence_items[0].context_text)
        self.assertEqual((asdict(self.criterion),asdict(self.bundle)),before)
        self.assertIn("Top-k",messages[0]["content"])

    def test_verification_claim_contains_condition_and_proposed_decision(self):
        result,_=self.execute();claim=grounding_draft_claim(self.criterion,result.data.conditions[0])
        self.assertIn(self.criterion.conditions[0].text,claim.claim_text)
        self.assertIn("충족",claim.claim_text)
        self.assertEqual(claim.evidence_refs,["source"])

    def test_quote_catalog_covers_context_exactly_with_unicode_and_long_lines(self):
        context = "가나다🙂\n" + " 긴 줄" * 700 + "\n마지막 표 단위: 백만원"
        self.bundle.evidence_items[0].context_text = context
        quotes = quote_options(self.bundle)
        self.assertEqual("".join(q.text for q in quotes), context)
        self.assertTrue(all(len(q.text) <= 600 for q in quotes))
        self.assertEqual(len({q.quote_id for q in quotes}), len(quotes))
        for q in quotes:
            self.assertEqual(context[q.context_start:q.context_end], q.text)

    def test_obvious_korean_nested_logic_is_not_flattened(self):
        from rag.nodes._criteria_support import explicit_unsupported_logic
        self.assertTrue(explicit_unsupported_logic("A와 (B 또는 C)를 모두 충족"))
        self.assertTrue(explicit_unsupported_logic("A 또는 (B와 C)를 충족"))
        self.assertFalse(explicit_unsupported_logic("A 또는 B를 충족"))

    def test_flat_all_any_three_valued_truth_tables(self):
        met,no,unknown=CriterionVerdict.MET,CriterionVerdict.NOT_MET,CriterionVerdict.UNDETERMINED
        for values in product((met,no,unknown),repeat=3):
            self.assertEqual(aggregate("all",values),no if no in values else unknown if unknown in values else met)
            self.assertEqual(aggregate("any",values),met if met in values else unknown if unknown in values else no)
        with self.assertRaises(ValueError):aggregate("all",[])
        with self.assertRaises(ValueError):aggregate("nested",[met])


if __name__=="__main__":unittest.main()
