"""자료함 업로드·보존·검색 범위와 웹 T-01을 실제 모델 호출 없이 검사한다."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from rag.nodes.draft_answer import DraftAnswerNode
from rag.web.workspace import Workspace, MAX_UPLOAD_BYTES
from scripts.run_rag_demo import Demo, make_handler

ROOT = Path(__file__).resolve().parents[1]
PDF_PYTHON = ROOT / ".venv/bin/python"


def fixture(root):
    text = "2025년 budget 사업비는 120백만원이다."
    locator = dict(type="pdf_page_bbox", pdf_page=1, bbox=[0, 0, 100, 20], units="pt",
                   coordinate_space="pymupdf_unrotated_page")
    block = dict(block_id="b1", doc_id="seed", version_sha256="v1", pdf_page=1, text=text,
                 source_locator=locator, extraction_method="native_text", verification_status="unverified")
    chunk = dict(chunk_id="source", doc_id="seed", version_sha256="v1", text=text, search_text=text,
        pdf_pages=[1], source_block_ids=["b1"], document_title="기본 문서",
        source_spans=[dict(block_id="b1", pdf_page=1, start_char=0, end_char=len(text), source_locator=locator)])
    chunks, blocks = root / "chunks.jsonl", root / "blocks.jsonl"
    chunks.write_text(json.dumps(chunk, ensure_ascii=False) + "\n", encoding="utf-8")
    blocks.write_text(json.dumps(block, ensure_ascii=False) + "\n", encoding="utf-8")
    return Demo(chunks, blocks, None)


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf_data = None
        if PDF_PYTHON.exists():
            code = """import pymupdf,sys
d=pymupdf.open();p=d.new_page();p.insert_text((60,80),'Project Aurora budget 200 in 2025.');d.new_page();sys.stdout.buffer.write(d.tobytes())
"""
            result = subprocess.run([str(PDF_PYTHON), "-c", code], capture_output=True, check=True)
            cls.pdf_data = result.stdout

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.generator = DraftAnswerNode("mock-model", "http://localhost:11434")
        self.seed = fixture(self.root)
        self.manager = Workspace(self.root/"library", Demo, seed=self.seed,
            pdf_python=str(PDF_PYTHON) if self.pdf_data else None, draft_node=self.generator)
        guard = patch("rag.llm.ollama_client.urlopen", side_effect=AssertionError("No live calls"))
        guard.start(); self.addCleanup(guard.stop)
        self.seed_id = self.manager.list_documents()["documents"][0]["id"]

    def tearDown(self):
        self.manager.close()
        self.temporary.cleanup()

    def wait(self, id):
        limit = time.monotonic() + 25
        while time.monotonic() < limit:
            job = self.manager.job(id)
            if job["status"] in ("completed", "error"):
                return job
            time.sleep(.03)
        self.fail("Job did not finish")

    def upload(self):
        if not self.pdf_data:
            self.skipTest("PyMuPDF runtime not available")
        result = self.wait(self.manager.upload("Aurora.pdf", self.pdf_data)["id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        return result["result"]["id"]

    @staticmethod
    def model(*, messages, **_):
        payload = json.loads(messages[-1]["content"])
        if "claim" in payload:
            claim = payload["claim"]
            return json.dumps(dict(claim_id=claim["claim_id"], verdict="supported", reason="모의 판정"))
        item = payload["evidence"][0]
        return json.dumps(dict(claims=[dict(claim_text="모의 budget 답변", evidence_refs=[item["chunk_id"]])], unanswered_items=[]))

    def test_initial_catalog_no_paths_and_unknown_id_rejected(self):
        response = self.manager.list_documents()
        self.assertEqual(len(response["documents"]),1)
        self.assertNotIn(str(self.root),json.dumps(response))
        for id in ("../outside", "unknown", None):
            with self.assertRaises(ValueError): self.manager.info(id if id is not None else "none")

    def test_rename_archive_restore_preserves_source_and_persists(self):
        self.manager.update_document(self.seed_id,title="바꾼 자료 이름",active=False)
        self.assertTrue((self.root/"chunks.jsonl").exists())
        with self.assertRaises(ValueError): self.manager.search("budget",3,[self.seed_id])
        other = Workspace(self.root/"library", Demo, seed=self.seed)
        try:
            row = other.list_documents()["documents"][0]
            self.assertEqual((row["title"],row["active"]),("바꾼 자료 이름",False))
            other.update_document(self.seed_id,active=True)
            self.assertEqual(other.search("budget",3,[self.seed_id])["sources"][0]["title"],"바꾼 자료 이름")
        finally: other.close()

    def test_bad_metadata_or_selection_not_used_for_paths(self):
        for title in ("", " ", 42, "x"*121):
            with self.assertRaises(ValueError): self.manager.update_document(self.seed_id,title=title)
        for ids in ([],"string",["unknown"],[{}]):
            with self.assertRaises(ValueError): self.manager.search("budget",3,ids)
        with self.assertRaises(ValueError): self.manager._owned_path("../escape")
        with self.assertRaises(ValueError): self.manager._owned_path("/absolute")

    def test_upload_extracts_indexes_and_previews_original_blank_page(self):
        key=self.upload()
        info=self.manager.info(key)
        self.assertEqual(info["page_count"],2)
        record=self.manager.records[key]
        extraction=self.manager._owned_path(record["blocks"]).parent
        manifest=json.loads((extraction/"manifest.json").read_text())
        self.assertTrue(Path(manifest["local_file"]).is_file())
        report=json.loads((self.manager._owned_path(record["chunks"]).parent/"chunking_report.json").read_text())
        self.assertTrue(Path(report["input_dir"]).is_dir())
        self.assertTrue(Path(report["output_dir"]).is_dir())
        self.assertEqual(self.manager.list_documents()["documents"][-1]["no_native_text_pages"],[2])
        sources=self.manager.search("Aurora budget",3,[key])["sources"]
        self.assertTrue(sources)
        self.assertEqual(sources[0]["document_key"],key)
        self.assertIn("Aurora",sources[0]["text"])
        self.assertEqual(sources[0]["document_version"],info["version"])
        self.assertTrue(self.manager.preview(key,2).startswith(b"\x89PNG"))
        self.assertTrue(self.manager.preview(key,1,sources[0]["chunk_id"]).startswith(b"\x89PNG"))
        self.manager.update_document(key,active=False)
        duplicate=self.wait(self.manager.upload("same-content.pdf",self.pdf_data)["id"])
        self.assertEqual(duplicate["result"]["id"],key)
        self.assertEqual(len(self.manager.list_documents()["documents"]),2)

    def test_uploaded_records_reload_and_keep_source_references(self):
        key=self.upload()
        other=Workspace(self.root/"library",Demo,pdf_python=PDF_PYTHON)
        try:
            result=other.search("Aurora",3,[key])
            self.assertTrue(result["sources"])
            self.assertEqual(result["sources"][0]["source_spans"][0]["pdf_page"],1)
        finally: other.close()

    def test_selection_excludes_other_document(self):
        key=self.upload()
        self.assertFalse(self.manager.search("Aurora",3,[self.seed_id])["sources"])
        combined=self.manager.search("budget",3,[self.seed_id,key])
        self.assertEqual({source["document_key"] for source in combined["sources"]},{self.seed_id,key})

    def test_invalid_upload_is_not_added(self):
        if not self.pdf_data: self.skipTest("PyMuPDF runtime not available")
        for filename,content in (("../bad.pdf",self.pdf_data),("bad.txt",self.pdf_data),("bad.pdf",b"not PDF"),("big.pdf",b"%PDF-"+b"x"*MAX_UPLOAD_BYTES)):
            with self.assertRaises(ValueError): self.manager.upload(filename,content)
        job=self.wait(self.manager.upload("broken.pdf",b"%PDF-invalid file")["id"])
        self.assertEqual(job["status"],"error")
        self.assertEqual(len(self.manager.list_documents()["documents"]),1)

    def test_search_never_calls_model(self):
        with patch.object(self.generator.client,"chat") as model:
            result=self.manager.search("budget",3,[self.seed_id])
        model.assert_not_called(); self.assertEqual(result["kind"],"search")

    def test_web_qa_uses_t01_and_deduplicates_same_request(self):
        with patch.object(self.generator.client,"chat",side_effect=self.model) as model:
            first=self.manager.qa("budget",3,[self.seed_id],"web-request-1")
            second=self.manager.qa("budget",3,[self.seed_id],"web-request-1")
            self.assertEqual(first["id"],second["id"])
            job=self.wait(first["id"])
        self.assertEqual(job["status"],"completed",job.get("error"))
        data=job["result"]
        self.assertEqual(data["workflow_result"]["status"],"success")
        self.assertEqual(set(data["trace"]),{"n01","n02","n04","n03"})
        self.assertEqual(data["workflow_result"]["verified_claims"][0]["evidence_refs"],["source"])
        self.assertEqual(model.call_count,2)
        with self.assertRaises(ValueError): self.manager.qa("different",3,[self.seed_id],"web-request-1")

    def test_uploaded_pdf_is_actual_input_of_qa(self):
        key=self.upload()
        with patch.object(self.generator.client,"chat",side_effect=self.model):
            job=self.wait(self.manager.qa("Aurora budget",3,[key],"upload-qa")["id"])
        result=job["result"]["workflow_result"]
        self.assertEqual(result["verified_claims"][0]["sources"][0]["document_version"],self.manager.info(key)["version"])
        self.assertNotEqual(result["verified_claims"][0]["sources"][0]["document_id"],"seed")

    def test_empty_search_qa_has_no_model_calls(self):
        with patch.object(self.generator.client,"chat") as model:
            job=self.wait(self.manager.qa("zzzxqv999xxx",3,[self.seed_id],"empty-search")["id"])
        model.assert_not_called()
        self.assertEqual(job["result"]["workflow_result"]["outcome"],"insufficient_evidence")

    def test_model_update_exposes_no_key_and_clears_cached_drafts(self):
        self.manager.search("budget",3,[self.seed_id])
        with patch.dict("os.environ",{"OLLAMA_API_KEY":"mock-secret-key"}):
            info=self.manager.configure_model("mock-cloud","cloud")
        self.assertNotIn("mock-secret-key",json.dumps(info))
        self.assertEqual(len(self.manager.sessions),0)
        with self.assertRaises(ValueError): self.manager.configure_model(" ")

    def test_bounded_job_queue_rejects_overflow(self):
        for _ in range(4): self.manager._new_job("test")
        with self.assertRaises(ValueError): self.manager._new_job("extra")

    def test_http_routes_upload_and_origin_checks(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(self.manager))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base+"/api/documents") as response:
                self.assertEqual(json.load(response)["documents"][0]["id"],self.seed_id)
            body=json.dumps(dict(query="budget",document_ids=[self.seed_id])).encode()
            for headers in ({"Origin":"https://example.com"},{"Host":"evil.example"}):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+"/api/search",data=body,headers=headers))
                self.assertEqual(error.exception.code,403)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base+"/api/documents/upload?filename=large.pdf",data=b"",headers={"Content-Length":str(MAX_UPLOAD_BYTES+1)}))
            self.assertEqual(error.exception.code,413)
            if self.pdf_data:
                with urlopen(Request(base+"/api/documents/upload?filename=HTTP.pdf",data=self.pdf_data,
                                     headers={"Content-Type":"application/pdf"})) as response:
                    self.assertEqual(response.status,202); job=json.load(response)
                self.assertEqual(self.wait(job["id"])["status"],"completed")
            with urlopen(base+"/") as response:
                self.assertIn("문서와 대화",response.read().decode())
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == "__main__":
    unittest.main()
