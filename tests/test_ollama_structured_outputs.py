"""Ollama JSON Schema 요청과 노드별 응답 계약. 외부 모델 호출은 없다."""
from dataclasses import asdict
import io
import json
import unittest
from unittest.mock import patch

from rag.contracts import DraftClaim, EvidenceBundle, EvidenceItem, ReviewDraft, RevisionTarget
from rag.llm.ollama_client import OllamaClient
from rag.llm.response_schemas import draft_response_schema, verification_response_schema
from rag.nodes.draft_answer import DraftAnswerNode
from rag.nodes.verify_grounding import VerifyGroundingNode


class StructuredOutputTests(unittest.TestCase):
    def setUp(self):
        self.bundle=EvidenceBundle("req",[EvidenceItem("source-id","doc","v1",1,
            "2025년 사업비는 120백만원이다.",[{"block_id":"b1","source_locator":{"pdf_page":3,"bbox":[0,0,100,20]}}],False)],"test")
        self.node=DraftAnswerNode("test-model","http://localhost:11434")
        self.raw=json.dumps(dict(claims=[dict(claim_text="사업비는 120백만원이다.",evidence_refs=["source-id"])],unanswered_items=[]))

    def fake(self, content):
        return io.BytesIO(json.dumps({"message":{"content":content}}).encode())

    def test_local_payload_contains_schema_and_zero_temperature(self):
        schema=draft_response_schema(["source-id"])
        with patch("rag.llm.ollama_client.urlopen",return_value=self.fake(self.raw)) as transport:
            self.node.client.chat(messages=[{"role":"user","content":"자료"}],response_format=schema)
        payload=json.loads(transport.call_args.args[0].data)
        self.assertEqual(payload["format"],schema)
        self.assertEqual(payload["options"]["temperature"],0)
        self.assertFalse(payload["stream"])

    def test_cloud_does_not_send_unsupported_format_or_key_in_payload(self):
        client=OllamaClient("test","https://ollama.com",provider="cloud",api_key="mock-secret")
        with patch("rag.llm.ollama_client.urlopen",return_value=self.fake(self.raw)) as transport:
            client.chat(messages=[],response_format=draft_response_schema(["source-id"]))
        request=transport.call_args.args[0];payload=json.loads(request.data)
        self.assertNotIn("format",payload)
        self.assertNotIn("options",payload)
        self.assertNotIn("mock-secret",json.dumps(payload))
        self.assertEqual(request.get_header("Authorization"),"Bearer mock-secret")

    def test_local_context_is_explicit_and_configurable(self):
        for size in (8192, 16384):
            client = OllamaClient("test", "http://localhost:11434", num_ctx=size)
            with patch("rag.llm.ollama_client.urlopen", return_value=self.fake("응답")) as transport:
                client.chat(messages=[], response_format="json")
            payload = json.loads(transport.call_args.args[0].data)
            self.assertEqual(payload["options"]["num_ctx"], size)
            self.assertEqual(payload["options"]["temperature"], 0)

    def test_invalid_local_context_is_rejected_before_network(self):
        for size in (True, 0, 511, "16384"):
            client = OllamaClient("test", "http://localhost:11434", num_ctx=size)
            with patch("rag.llm.ollama_client.urlopen") as transport:
                with self.assertRaises(ValueError): client.chat(messages=[])
                transport.assert_not_called()

    def test_unstructured_client_call_remains_compatible(self):
        with patch("rag.llm.ollama_client.urlopen",return_value=self.fake("일반 응답")) as transport:
            value=self.node.client.chat(messages=[])
        self.assertEqual(value,"일반 응답")
        self.assertNotIn("format",json.loads(transport.call_args.args[0].data))

    def test_invalid_format_is_rejected_before_network(self):
        with patch("rag.llm.ollama_client.urlopen") as transport:
            with self.assertRaises(ValueError):self.node.client.chat(messages=[],response_format="unsupported")
            transport.assert_not_called()

    def test_generation_uses_separate_system_and_data_messages(self):
        with patch.object(self.node.client,"chat",return_value=self.raw) as model:
            result=self.node.run("req","2025년 사업비는?",self.bundle)
        self.assertEqual(result.status,"success")
        messages=model.call_args.kwargs["messages"]
        self.assertEqual([message["role"] for message in messages],["system","user"])
        data=json.loads(messages[1]["content"])
        self.assertEqual(data["question"],"2025년 사업비는?")
        self.assertEqual(data["evidence"][0]["context_text"],self.bundle.evidence_items[0].context_text)
        self.assertNotIn("source_refs",data["evidence"][0])
        self.assertIn("source_refs",asdict(self.bundle)["evidence_items"][0])
        self.assertIn('"required"',messages[0]["content"])
        self.assertEqual(model.call_args.kwargs["response_format"],draft_response_schema(["source-id"]))

    def test_initial_schema_forbids_model_assigned_ids_and_unknown_sources(self):
        item=draft_response_schema(["source-id","source-id"])["properties"]["claims"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertNotIn("claim_id",item["properties"])
        self.assertEqual(item["properties"]["evidence_refs"]["items"]["enum"],["source-id"])

    def test_revision_schema_retains_only_existing_target_ids(self):
        target=RevisionTarget("claim_009","틀린 주장","이유",["source-id"])
        revised=json.dumps(dict(claims=[dict(claim_id="claim_009",claim_text="수정 주장",evidence_refs=["source-id"])],unanswered_items=[]))
        with patch.object(self.node.client,"chat",return_value=revised) as model:
            result=self.node.run("req","질문",self.bundle,revision_targets=[target])
        self.assertEqual(result.data.claims[0].claim_id,"claim_009")
        item=model.call_args.kwargs["response_format"]["properties"]["claims"]["items"]
        self.assertEqual(item["properties"]["claim_id"]["enum"],["claim_009"])

    def test_plain_metadata_explanation_still_fails_closed_with_clear_reason(self):
        raw="제공된 JSON 데이터는 PDF 텍스트 블록의 메타데이터를 나타냅니다."
        with patch.object(self.node.client,"chat",return_value=raw):
            result=self.node.run("req","문서를 설명해줘",self.bundle)
        self.assertEqual(result.status,"error")
        diagnostic=result.diagnostics["parse_and_validate_draft"]
        self.assertEqual(diagnostic.error_code,"INVALID_DRAFT_RESPONSE")
        self.assertIn("JSON 객체 형식",diagnostic.reason)
        self.assertNotIn(raw,json.dumps(asdict(result),ensure_ascii=False))

    def test_schema_does_not_replace_reference_validation(self):
        invalid=json.dumps(dict(claims=[dict(claim_text="내용",evidence_refs=["invented"])],unanswered_items=[]))
        with patch.object(self.node.client,"chat",return_value=invalid):
            result=self.node.run("req","질문",self.bundle)
        self.assertEqual(result.status,"error")

    def test_verification_schema_pins_claim_id_without_weakening_parser(self):
        schema = verification_response_schema("claim_0001")
        self.assertEqual(schema["properties"]["claim_id"]["enum"], ["claim_0001"])
        node = VerifyGroundingNode(self.node.client)
        claim = DraftClaim("claim_0001", "사업비는 120백만원이다.", ["source-id"])
        raw = json.dumps(dict(claim_id="b1", verdict="supported", reason="근거 지지"))
        with self.assertRaises(ValueError):
            node._parse_verification(raw, claim, self.bundle.evidence_items)

    def test_verifier_keeps_full_text_but_omits_repeated_locator_metadata(self):
        node = VerifyGroundingNode(self.node.client)
        claim = DraftClaim("claim_0001", "사업비", ["source-id"])
        before = asdict(self.bundle)
        messages = node._build_verification_messages(claim, self.bundle.evidence_items, {})
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(payload["evidence"][0]["context_text"], self.bundle.evidence_items[0].context_text)
        self.assertNotIn("source_refs", payload["evidence"][0])
        self.assertEqual(asdict(self.bundle), before)

    def test_shared_client_has_no_schema_state_leak_between_nodes(self):
        seen=[]
        def transport(request,timeout):
            payload=json.loads(request.data);seen.append(payload)
            content=(self.raw if "claims" in payload["format"]["properties"] else
                     json.dumps(dict(claim_id="claim_0001",verdict="supported",reason="근거 지지")))
            return self.fake(content)
        with patch("rag.llm.ollama_client.urlopen",side_effect=transport):
            draft=self.node.run("req","2025년 사업비는?",self.bundle)
            report=VerifyGroundingNode(self.node.client).run("req",draft.data,self.bundle)
        self.assertEqual(report.status,"success")
        self.assertEqual(len(seen),2)
        self.assertIn("claims",seen[0]["format"]["properties"])
        self.assertEqual(seen[1]["format"],verification_response_schema("claim_0001"))
        self.assertNotIn("claims",seen[1]["format"]["properties"])


if __name__=="__main__":unittest.main()
