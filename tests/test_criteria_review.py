"""T-02의 실제 노드·LangGraph 연결을 모의 모델 응답으로 검증한다."""
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
    CriteriaReviewTask, CriterionVerdict, DocumentRef, EvidenceBundle, EvidenceItem, NodeResult,
    RetrievalCandidate, RetrievalCandidates, StepDiagnostic, Verdict,
)
from rag.llm.ollama_client import OllamaClient
from rag.workflows.criteria_review import CriteriaReviewWorkflow, main


class CriteriaReviewTests(unittest.TestCase):
    def setUp(self):
        self.client=OllamaClient("mock","http://localhost:11434")
        self.task=CriteriaReviewTask("req","기준을 검토해줘",["사업비 100백만원 이상"],[DocumentRef("doc","v1")])
        self.workflow=CriteriaReviewWorkflow(None,{}, {},self.client)
        self.retrieval=NodeResult("success",RetrievalCandidates("req",[
            RetrievalCandidate("source","doc","v1",1,{"bm25_score":1.0})],{},"bm25-baseline-v1"),{},None)
        self.bundle=EvidenceBundle("req",[EvidenceItem("source","doc","v1",1,
            "사업비는 120백만원이다.",[{"block_id":"b1","source_locator":{"pdf_page":3,"bbox":[0,0,10,10]}}],False)],"fixed-context-v1")
        self.structure_rows=None;self.proposed="met";self.judged="supported";self.calls=[]
        self.no_network=patch("rag.llm.ollama_client.urlopen",side_effect=AssertionError("실제 모델 호출 금지"))
        self.no_network.start();self.addCleanup(self.no_network.stop)

    def fake_model(self,*,messages,response_format):
        payload=json.loads(messages[-1]["content"]);self.calls.append((deepcopy(payload),deepcopy(response_format)))
        if "criteria" in payload:
            rows=self.structure_rows
            if rows is None:rows=[dict(input_index=c["input_index"],status="structured",operator="all",
                conditions=[dict(text=c["original_text"],source_quote=c["original_text"])],reason=None) for c in payload["criteria"]]
            return json.dumps(dict(items=rows))
        if "criterion" in payload:
            quote_ids=[q["quote_id"] for e in payload["evidence"] for q in e["passages"]]
            return json.dumps(dict(conditions=[dict(condition_id=c["condition_id"],verdict=self.proposed,
                claim_text="사업비는 120백만원이다.",reason="모의 판정",quote_ids=quote_ids)
                for c in payload["criterion"]["conditions"]]))
        return json.dumps(dict(claim_id=payload["claim"]["claim_id"],verdict=self.judged,reason="모의 근거 검증"))

    def execute(self,*,retrieval=None,context=None,model=None):
        retrieval_args={"return_value":self.retrieval} if retrieval is None else (
            {"side_effect":retrieval} if isinstance(retrieval,list) else {"return_value":retrieval})
        context_args={"return_value":NodeResult("success",self.bundle,{},None)} if context is None else (
            {"side_effect":context} if isinstance(context,list) else {"return_value":context})
        with patch("rag.workflows.criteria_review.retrieve_evidence",**retrieval_args) as search, \
             patch("rag.workflows.criteria_review.get_source_context",**context_args) as source, \
             patch.object(self.client,"chat",side_effect=model or self.fake_model) as call:
            state=self.workflow.run_with_state(self.task)
        return state,search,source,call

    def error(self,code="MODEL_TIMEOUT"):
        return NodeResult("error",None,{"step":StepDiagnostic("failed",error_code=code,retryable=True)},None)

    def test_complete_flow_reuses_all_nodes_and_preserves_provenance(self):
        before=asdict(self.task),asdict(self.bundle)
        state,search,source,call=self.execute();result=state["result"]
        self.assertEqual((result.status,result.outcome,result.errors),("success",None,[]))
        finding=result.findings[0];condition=finding.conditions[0]
        self.assertEqual(finding.verdict,CriterionVerdict.MET)
        self.assertEqual(condition.verification.verdict,Verdict.SUPPORTED)
        self.assertEqual(condition.sources[0].source_refs,self.bundle.evidence_items[0].source_refs)
        self.assertEqual(condition.sources[0].document_version,"v1")
        self.assertEqual(condition.verified_claim_text,"사업비는 120백만원이다.")
        self.assertEqual(condition.claim_id,state["records"][0]["n06"].data.conditions[0].claim_id)
        self.assertEqual(search.call_args.args[0].search_text,self.task.criteria_texts[0])
        self.assertEqual((asdict(self.task),asdict(self.bundle)),before)
        self.assertEqual(call.call_count,3);search.assert_called_once();source.assert_called_once()

    def test_direct_negative_remains_business_not_met_when_supported(self):
        self.proposed="not_met"
        state,*_=self.execute();r=state["result"]
        self.assertEqual((r.status,r.findings[0].verdict),("success",CriterionVerdict.NOT_MET))

    def test_contradicted_and_insufficient_findings_downgrade_without_retry(self):
        for verdict in ("contradicted","insufficient"):
            self.calls=[];self.judged=verdict
            state,search,source,call=self.execute();r=state["result"]
            self.assertEqual((r.status,r.outcome),("success","insufficient_evidence"))
            self.assertEqual(r.findings[0].verdict,CriterionVerdict.UNDETERMINED)
            self.assertIsNone(r.findings[0].conditions[0].verified_claim_text)
            self.assertEqual(r.findings[0].conditions[0].proposed_verdict,CriterionVerdict.MET)
            self.assertEqual(call.call_count,3);search.assert_called_once();source.assert_called_once()

    def test_no_hits_are_unknown_and_skip_context_evaluation_verification(self):
        self.retrieval.data.candidates=[];self.retrieval.outcome="no_candidates"
        state,_,source,call=self.execute();r=state["result"]
        self.assertEqual((r.status,r.outcome),("success","insufficient_evidence"))
        self.assertEqual(r.findings[0].verdict,CriterionVerdict.UNDETERMINED)
        self.assertEqual(call.call_count,1);source.assert_not_called()
        self.assertNotIn("n03",state["records"][0])

    def test_no_context_skips_generation_and_is_not_negative(self):
        self.bundle.evidence_items=[]
        state,_,_,call=self.execute();self.assertEqual(call.call_count,1)
        self.assertEqual(state["result"].findings[0].verdict,CriterionVerdict.UNDETERMINED)

    def test_no_criteria_skips_every_model_and_retrieval(self):
        self.task.criteria_texts=[]
        state,search,source,call=self.execute()
        self.assertEqual((state["result"].status,state["result"].outcome),("success","needs_clarification"))
        search.assert_not_called();source.assert_not_called();call.assert_not_called()

    def test_all_unsupported_or_ambiguous_skips_retrieval(self):
        for code in ("unsupported_structure","needs_clarification"):
            self.structure_rows=[dict(input_index=0,status=code,operator=None,conditions=[],reason="보류 이유")]
            state,search,source,call=self.execute()
            self.assertEqual((state["result"].status,state["result"].outcome),("success",code))
            self.assertEqual(len(state["result"].unprocessed_criteria),1)
            search.assert_not_called();source.assert_not_called();self.assertEqual(call.call_count,1)

    def test_independent_partial_criteria_preserve_unprocessed_raw(self):
        self.task.criteria_texts.append("A AND (B OR C)")
        self.structure_rows=[dict(input_index=0,status="structured",operator="all",conditions=[
            dict(text=self.task.criteria_texts[0],source_quote=self.task.criteria_texts[0])],reason=None),
            dict(input_index=1,status="unsupported_structure",operator=None,conditions=[],reason="중첩")]
        state,search,_,_=self.execute();r=state["result"]
        self.assertEqual((r.status,r.outcome),("success","partial_criteria"))
        self.assertEqual(r.unprocessed_criteria[0].original_text,self.task.criteria_texts[1])
        search.assert_called_once()

    def test_multiple_criteria_do_not_mix_same_chunk_id_different_context(self):
        self.task.criteria_texts.append("사업비 200백만원 미만")
        other=deepcopy(self.bundle);other.evidence_items[0].context_text="사업비는 130백만원이다."
        contexts=[NodeResult("success",self.bundle,{},None),NodeResult("success",other,{},None)]
        state,search,_,call=self.execute(context=contexts)
        self.assertEqual(len(state["result"].findings),2)
        judge_inputs=[p for p,s in self.calls if "claim" in p]
        self.assertEqual([p["evidence"][0]["context_text"] for p in judge_inputs],["사업비는 120백만원이다.","사업비는 130백만원이다."])
        ids=[f.conditions[0].claim_id for f in state["result"].findings]
        self.assertEqual(len(set(ids)),2);self.assertEqual(search.call_count,2);self.assertEqual(call.call_count,5)

    def test_failed_second_search_does_not_reuse_previous_context(self):
        self.task.criteria_texts.append("사업비 200백만원 미만")
        state,_,source,call=self.execute(retrieval=[self.retrieval,self.error("SEARCH_FAILED")])
        r=state["result"]
        self.assertEqual((r.status,len(r.errors)),("partial",1))
        self.assertEqual(r.findings[0].verdict,CriterionVerdict.MET)
        self.assertEqual(r.findings[1].verdict,CriterionVerdict.UNDETERMINED)
        self.assertEqual(r.findings[1].conditions[0].sources,[])
        self.assertIsNone(state["records"][1]["n02"])
        source.assert_called_once();self.assertEqual(call.call_count,3)
        self.assertEqual(r.errors[0].node,"N-01")

    def test_all_required_search_failures_are_execution_error(self):
        state,_,_,call=self.execute(retrieval=self.error("SEARCH_FAILED"))
        self.assertEqual((state["result"].status,state["result"].outcome),("error",None))
        self.assertEqual(call.call_count,1)

    def test_context_failure_not_recast_as_business_insufficiency(self):
        state,_,_,call=self.execute(context=self.error("SOURCE_FAILED"))
        self.assertEqual(state["result"].status,"error");self.assertEqual(call.call_count,1)

    def test_partial_retrieval_preserves_result_and_execution_diagnostic(self):
        self.retrieval.status="partial"
        self.retrieval.diagnostics={"dense_search":StepDiagnostic("failed",error_code="DENSE_FAILED",retryable=True)}
        state,*_=self.execute();r=state["result"]
        self.assertEqual((r.status,r.findings[0].verdict),("partial",CriterionVerdict.MET))
        self.assertTrue(r.evidence_incomplete)

    def test_evaluation_unknown_skips_verifier_and_is_success(self):
        self.proposed="undetermined"
        state,_,_,call=self.execute()
        self.assertEqual(state["result"].status,"success");self.assertEqual(call.call_count,2)
        self.assertNotIn("n03",state["records"][0])

    def test_evaluation_timeout_and_judge_timeout_stay_execution_errors(self):
        for key in ("criterion","claim"):
            def model(*,messages,response_format):
                if key in json.loads(messages[-1]["content"]):raise TimeoutError("private text")
                return self.fake_model(messages=messages,response_format=response_format)
            state,*_=self.execute(model=model);r=state["result"]
            self.assertEqual(r.status,"error");self.assertTrue(r.errors[0].retryable)
            self.assertNotIn("private text",json.dumps(asdict(r),ensure_ascii=False))

    def test_wrong_model_judge_id_and_unknown_references_fail_closed(self):
        def model(*,messages,response_format):
            p=json.loads(messages[-1]["content"])
            if "claim" in p:return json.dumps(dict(claim_id="foreign",verdict="supported",reason="invalid"))
            return self.fake_model(messages=messages,response_format=response_format)
        state,*_=self.execute(model=model)
        self.assertEqual(state["result"].status,"error")
        self.assertEqual(state["result"].errors[0].error_code,"INVALID_VERIFICATION_RESPONSE")

    def test_partial_judge_failure_preserves_completed_condition(self):
        self.task.criteria_texts = ["사업비와 인건비를 검토"]
        self.structure_rows = [dict(input_index=0, status="structured", operator="all", conditions=[
            dict(text="사업비 검토", source_quote="사업비"),
            dict(text="인건비 검토", source_quote="인건비")], reason=None)]
        def model(*, messages, response_format):
            payload = json.loads(messages[-1]["content"])
            if "claim" in payload and "condition_02" in payload["claim"]["claim_id"]:
                raise TimeoutError("private token")
            return self.fake_model(messages=messages, response_format=response_format)
        state, *_ = self.execute(model=model)
        result = state["result"]
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.findings[0].conditions[0].verdict, CriterionVerdict.MET)
        self.assertEqual(result.findings[0].conditions[1].verdict, CriterionVerdict.UNDETERMINED)
        self.assertEqual(result.findings[0].verdict, CriterionVerdict.UNDETERMINED)
        self.assertTrue(result.errors[0].retryable)
        self.assertNotIn("private token", json.dumps(asdict(result)))

    def test_invalid_task_rejected_before_any_execution(self):
        for field,value in (("criteria_texts",None),("request_ref"," "),("document_scope",["bad"]),
                            ("context_profile","unknown"),("retrieval_profile","unknown")):
            task=deepcopy(self.task);setattr(task,field,value)
            with patch.object(self.workflow.structure_node,"run") as node:
                r=self.workflow.run(task)
            self.assertEqual(r.status,"error");node.assert_not_called()

    def test_out_of_scope_retrieval_and_context_are_contract_errors(self):
        self.retrieval.data.candidates[0].document_version="v2"
        state,_,source,call=self.execute()
        self.assertEqual(state["result"].errors[0].error_code,"NODE_CONTRACT_VIOLATION")
        source.assert_not_called();self.assertEqual(call.call_count,1)
        self.retrieval.data.candidates[0].document_version="v1"
        self.bundle.evidence_items[0].document_version="v2"
        state,*_=self.execute()
        self.assertEqual(state["result"].errors[0].node,"N-02")

    def test_llm_silent_criterion_omission_aborts_before_search(self):
        self.task.criteria_texts.append("별도 기준");self.structure_rows=[]
        state,search,_,_=self.execute()
        self.assertEqual(state["result"].status,"error");search.assert_not_called()

    def test_repeated_runs_use_fresh_state_and_do_not_mutate_prior_result(self):
        first,*_=self.execute();snapshot=asdict(first["result"])
        self.proposed="not_met";second,*_=self.execute()
        self.assertEqual(asdict(first["result"]),snapshot)
        self.assertEqual(second["result"].findings[0].verdict,CriterionVerdict.NOT_MET)

    def test_and_or_are_recomputed_after_grounding(self):
        self.task.criteria_texts=["사업비와 인건비를 검토"]
        for op, expected in (("all",CriterionVerdict.UNDETERMINED),("any",CriterionVerdict.MET)):
            self.structure_rows=[dict(input_index=0,status="structured",operator=op,conditions=[
                dict(text="사업비 검토",source_quote="사업비"),dict(text="인건비 검토",source_quote="인건비")],reason=None)]
            def model(*,messages,response_format):
                p=json.loads(messages[-1]["content"])
                if "claim" in p:
                    verdict="supported" if "condition_01" in p["claim"]["claim_id"] else "contradicted"
                    return json.dumps(dict(claim_id=p["claim"]["claim_id"],verdict=verdict,reason="모의 근거 검증"))
                return self.fake_model(messages=messages,response_format=response_format)
            state,*_=self.execute(model=model)
            self.assertEqual(state["result"].findings[0].verdict,expected)
            self.assertEqual(state["result"].findings[0].conditions[1].verdict,CriterionVerdict.UNDETERMINED)


class CriteriaCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory();root=Path(cls.directory.name)
        texts=["사업비는 120백만원이다.","별도 시설 관리 계획", "연구 평가 목표"]
        blocks,chunks=[],[]
        for i,text in enumerate(texts,1):
            locator=dict(type="pdf_page_bbox",pdf_page=i,bbox=[0,0,100,20],units="pt",coordinate_space="pymupdf_unrotated_page")
            blocks.append(dict(block_id=f"b{i}",doc_id="doc",version_sha256="v1",pdf_page=i,text=text,
                               source_locator=locator,extraction_method="native_text",verification_status="unverified"))
            chunks.append(dict(chunk_id=f"c{i}",doc_id="doc",version_sha256="v1",text=text,search_text=text,
                pdf_pages=[i],source_spans=[dict(block_id=f"b{i}",pdf_page=i,start_char=0,end_char=len(text),source_locator=locator)],
                source_block_ids=[f"b{i}"],document_title="데모"))
        cls.chunks=root/'chunks.jsonl';cls.blocks=root/'blocks.jsonl'
        cls.chunks.write_text('\n'.join(json.dumps(c,ensure_ascii=False) for c in chunks))
        cls.blocks.write_text('\n'.join(json.dumps(b,ensure_ascii=False) for b in blocks))
        cls.args=['--chunks',str(cls.chunks),'--blocks',str(cls.blocks),'--scope','doc','v1','--request-ref','cli','--model','mock']

    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()

    def test_cli_empty_criteria_returns_business_clarification_without_network(self):
        with patch('rag.llm.ollama_client.urlopen',side_effect=AssertionError('No calls')) as model,redirect_stdout(io.StringIO()) as out:
            code=main(self.args)
        self.assertEqual(code,0);self.assertEqual(json.loads(out.getvalue())['outcome'],'needs_clarification');model.assert_not_called()

    def test_cli_invalid_criteria_file_is_execution_error(self):
        with redirect_stdout(io.StringIO()) as out:code=main(self.args+['--criteria-file','/not/a/file'])
        self.assertEqual(code,2);self.assertEqual(json.loads(out.getvalue())['status'],'error')

    def test_cli_real_retrieval_source_and_trace_with_mock_model(self):
        trace=Path(self.directory.name)/'trace.json'
        def model(*,messages,response_format):
            p=json.loads(messages[-1]['content'])
            if 'criteria' in p:return json.dumps(dict(items=[dict(input_index=0,status='structured',operator='all',
                conditions=[dict(text='사업비 100백만원 이상',source_quote='사업비 100백만원 이상')],reason=None)]))
            if 'criterion' in p:
                e=next(e for e in p['evidence'] if any('120백만원' in q['text'] for q in e['passages']))
                return json.dumps(dict(conditions=[dict(condition_id=p['criterion']['conditions'][0]['condition_id'],
                    verdict='met',claim_text='사업비는 120백만원이다.',reason='기준 이상',quote_ids=[e['passages'][0]['quote_id']])]))
            return json.dumps(dict(claim_id=p['claim']['claim_id'],verdict='supported',reason='직접 근거'))
        with patch('rag.llm.ollama_client.OllamaClient.chat',side_effect=model),redirect_stdout(io.StringIO()) as out:
            code=main(self.args+['--criterion','사업비 100백만원 이상','--trace-out',str(trace)])
        result=json.loads(out.getvalue());self.assertEqual(code,0);self.assertEqual(result['findings'][0]['verdict'],'met')
        state=json.loads(trace.read_text());self.assertIn('n03',state['records'][0]);self.assertIn('n02',state['records'][0])
        self.assertEqual(result['findings'][0]['conditions'][0]['sources'][0]['document_id'],'doc')


if __name__=='__main__':unittest.main()
