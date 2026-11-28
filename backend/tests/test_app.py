"""통합 테스트: 합성 PDF → 업로드 → 잡 폴링 → 페이지 PNG·하이라이트 → BM25 검색 → 모의 Ollama QA.

실제 Ollama 서버에는 연결하지 않는다(httpx.MockTransport).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from reportanchor.app import create_app
from reportanchor.config import Settings
from reportanchor.llm import normalize_origin

BASE = "http://127.0.0.1:8765"
API_KEY = "test-secret-key-should-never-leak"
CONTRA = "CONTRADICTED_MARKER"


class FakeOllama:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append({"url": str(request.url), "headers": dict(request.headers), "body": body})
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        if "N-04" in system:
            ids = re.findall(r'chunk_id="(chk_[0-9a-f]+_\d+)"', user)
            first = ids[0]
            content = {"claims": [
                {"claim_text": "2024년 경상운영비는 4,532백만원이다.", "evidence_refs": [first]},
                {"claim_text": f"2024년 경상운영비는 9,999백만원이다. {CONTRA}", "evidence_refs": [first]},
                {"claim_text": "출처 없는 주장이다.", "evidence_refs": ["chk_fake_99999"]},
            ]}
            # Cloud는 format 미지원 → 코드 블록으로 감싼 응답도 처리되는지 확인
            text = "```json\n" + json.dumps(content, ensure_ascii=False) + "\n```"
        elif "N-03" in system:
            checks = []
            for cid, claim in re.findall(r"claim_id: (claim_\d+)\n주장: (.*)", user):
                verdict = "contradicted" if "9,999" in claim else "supported"
                checks.append({"claim_id": cid, "verdict": verdict, "reason": "근거 대조 결과"})
            text = json.dumps({"checks": checks}, ensure_ascii=False)
        else:
            return httpx.Response(400, json={"error": "unexpected"})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": text}})


def make_pdf() -> bytes:
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 80), "2024 예산 개요", fontname="korea", fontsize=14)
    p1.insert_text((72, 200), "2024년 경상운영비는 4,532백만원이다.", fontname="korea", fontsize=11)
    p1.insert_text((72, 400), "Operating budget details are listed in the appendix table.", fontsize=11)
    p2 = doc.new_page()
    p2.insert_text((72, 200), "2025년 경상운영비는 4,566백만원이다.", fontname="korea", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def wait_job(client: TestClient, job_id: str, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "error"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job timeout: {job_id}")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ra")
    fake = FakeOllama()
    settings = Settings(data_dir=tmp / "data", frontend_dist=tmp / "no-dist", port=8765, api_key=API_KEY,
                        autodetect_model=False)
    app = create_app(settings, ollama_transport=httpx.MockTransport(fake))
    with TestClient(app, base_url=BASE) as client:
        pdf = make_pdf()
        r = client.post("/api/documents/upload", files={"file": ("budget-report.pdf", pdf, "application/pdf")})
        assert r.status_code == 202, r.text
        job = wait_job(client, r.json()["job_id"])
        assert job["status"] == "completed", job
        yield {"client": client, "fake": fake, "settings": settings, "pdf": pdf, "doc_id": job["result"]["document_id"]}


def run_query(client: TestClient, **body) -> dict:
    r = client.post("/api/queries", json=body)
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "completed", job
    assert job["kind"] == "query"
    return job["result"]


# ---------------------------------------------------------------- 업로드·문서
def test_health_and_documents(env):
    c = env["client"]
    h = c.get("/api/health").json()
    assert h["ok"] is True and h["version"] == "0.1.0" and h["model_configured"] is False
    listing = c.get("/api/documents").json()
    assert listing["model"]["api_key_configured"] is True
    assert API_KEY not in json.dumps(listing)
    doc = next(d for d in listing["documents"] if d["document_id"] == env["doc_id"])
    assert doc["title"] == "budget-report" and doc["page_count"] == 2 and doc["chunk_count"] >= 3
    assert doc["active"] is True and doc["document_version"].startswith("sha256-")
    detail = c.get(f"/api/documents/{env['doc_id']}").json()
    assert detail["block_count"] >= 4 and detail["pdf_available"] is True


def test_duplicate_upload_raw_body(env):
    c = env["client"]
    r = c.post("/api/documents/upload?filename=again.pdf", content=env["pdf"],
               headers={"Content-Type": "application/pdf"})
    assert r.status_code == 202
    body = r.json()
    assert body["duplicate"] is True and body["document_id"] == env["doc_id"]
    assert wait_job(c, body["job_id"])["status"] == "completed"
    assert len(c.get("/api/documents").json()["documents"]) == 1


def test_upload_rejects_non_pdf(env):
    c = env["client"]
    r = c.post("/api/documents/upload", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 400
    r = c.post("/api/documents/upload", files={"file": ("x.txt", env["pdf"], "text/plain")})
    assert r.status_code == 400
    r = c.post("/api/documents/upload", content=b"%PDF-1.7", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_patch_document_and_scope_check(env):
    c = env["client"]
    r = c.patch(f"/api/documents/{env['doc_id']}", json={"title": "예산 보고서", "active": False})
    assert r.status_code == 200 and r.json()["title"] == "예산 보고서" and r.json()["active"] is False
    r = c.post("/api/queries", json={"question": "경상운영비", "document_ids": [env["doc_id"]]})
    assert r.status_code == 400
    r = c.post("/api/queries", json={"question": "경상운영비", "document_ids": ["doc_unknown"]})
    assert r.status_code == 400
    r = c.patch(f"/api/documents/{env['doc_id']}", json={"active": True})
    assert r.json()["active"] is True


# ---------------------------------------------------------------- 원문 조회·렌더링
def test_search_chunk_and_page_highlight(env):
    c = env["client"]
    review = run_query(c, question="2024년 경상운영비", document_ids=[env["doc_id"]], mode="search")
    assert review["schema_version"] == "1.0" and review["mode"] == "search"
    assert review["thread_status"] == "complete" and review["status_label"] == "답변 완료"
    top = review["sources"][0]
    assert "4,532" in top["excerpt"] and top["pages"] == [1]
    assert review["sources"][0]["document_title"] == "예산 보고서"

    chunk = c.get(f"/api/documents/{env['doc_id']}/chunks/{top['chunk_id']}").json()
    assert chunk["pages"] == [1]
    ref = chunk["source_refs"][0]
    assert set(ref) == {"block_id", "page", "start_offset", "end_offset", "bbox"} and len(ref["bbox"]) == 4
    assert ref["start_offset"] == 0 and ref["end_offset"] > 0
    assert all(set(e) == {"block_id", "page", "start_offset", "end_offset"} for e in chunk["expanded_source_refs"])
    assert chunk["expanded_source_refs"], "고정 길이 앞뒤 확장이 있어야 함"
    assert "4,532" in chunk["context_text"]

    plain = c.get(f"/api/documents/{env['doc_id']}/pages/1?scale=1").content
    hl = c.get(f"/api/documents/{env['doc_id']}/pages/1?chunk_id={top['chunk_id']}&highlight=1&scale=1")
    assert hl.headers["content-type"] == "image/png" and hl.content.startswith(b"\x89PNG")
    assert plain.startswith(b"\x89PNG") and plain != hl.content
    off = c.get(f"/api/documents/{env['doc_id']}/pages/1?chunk_id={top['chunk_id']}&highlight=0&scale=1").content
    assert off == plain
    assert c.get(f"/api/documents/{env['doc_id']}/pages/9").status_code == 404


def test_page_text(env):
    c = env["client"]
    r = c.get(f"/api/documents/{env['doc_id']}/pages/1/text")
    assert r.status_code == 200
    body = r.json()
    assert body["document_id"] == env["doc_id"] and body["page"] == 1
    assert "2024 예산 개요\n\n2024년 경상운영비는 4,532백만원이다." in body["text"]
    assert "4,566" not in body["text"]  # 2쪽 내용은 섞이지 않음
    assert c.get(f"/api/documents/{env['doc_id']}/pages/2/text").json()["text"] == "2025년 경상운영비는 4,566백만원이다."
    assert c.get(f"/api/documents/{env['doc_id']}/pages/0/text").status_code == 404
    assert c.get(f"/api/documents/{env['doc_id']}/pages/3/text").status_code == 404
    assert c.get("/api/documents/doc_unknown/pages/1/text").status_code == 404


def test_search_no_hits(env):
    review = run_query(env["client"], question="qqqzzz", document_ids=[env["doc_id"]], mode="search")
    assert review["thread_status"] == "insufficient" and review["sources"] == []


# ---------------------------------------------------------------- QA
def test_qa_without_model_returns_error_with_sources(env):
    before = len(env["fake"].calls)
    review = run_query(env["client"], question="2024년 경상운영비는 얼마인가", document_ids=[env["doc_id"]])
    assert review["thread_status"] == "error" and review["status_label"] == "오류"
    assert review["sources"] and review["error_message"]
    assert len(env["fake"].calls) == before


def test_allowlist_blocks_unknown_hosts(env):
    c = env["client"]
    for bad in ("http://evil.example.com:11434", "http://127.0.0.1:11434@evil.example.com",
                "http://127.0.0.1:11435", "https://ollama.com/api/chat", "file:///etc/passwd"):
        r = c.post("/api/model", json={"provider": "local", "model": "m", "base_url": bad})
        assert r.status_code == 400, bad
    assert normalize_origin("HTTPS://Ollama.com:443/") == "https://ollama.com"


def test_qa_cloud_with_contradiction_removed(env):
    c = env["client"]
    r = c.post("/api/model", json={"provider": "cloud", "model": "deepseek-v4.1-flash"})
    assert r.status_code == 200, r.text
    assert r.json() == {"provider": "cloud", "base_url": "https://ollama.com", "model": "deepseek-v4.1-flash",
                        "enabled": True, "api_key_configured": True}
    assert c.get("/api/health").json()["ollama_mode"] == "cloud"

    before = len(env["fake"].calls)
    review = run_query(c, question="2024년 경상운영비는 얼마인가", document_ids=[env["doc_id"]], request_ref="req_test_1")
    calls = env["fake"].calls[before:]
    assert len(calls) == 2  # N-04 초안 1회 + N-03 독립 검증 1회
    assert all(call["url"] == "https://ollama.com/api/chat" for call in calls)
    assert all(call["headers"]["authorization"] == f"Bearer {API_KEY}" for call in calls)
    assert "format" not in calls[0]["body"]  # Cloud에는 format 필드를 보내지 않음
    assert "N-04" not in calls[1]["body"]["messages"][0]["content"]  # 검증은 별도 세션

    assert review["request_ref"] == "req_test_1"
    assert review["thread_status"] == "partial" and review["status_label"] == "일부 확인"
    assert review["evidence_incomplete"] is True and review["evidence_notice"]
    assert [v["verdict"] for v in review["verified_claims"]] == ["supported"]
    assert "4,532" in review["summary_text"]
    verdicts = {u["verdict"]: u for u in review["unconfirmed_items"]}
    assert verdicts["contradicted"]["claim_text"] is None
    assert verdicts["insufficient"]["evidence_refs"] == []
    dumped = json.dumps(review, ensure_ascii=False)
    assert CONTRA not in dumped and "9,999" not in dumped  # 모순 원문 서버측 제거
    assert review["sources"] and isinstance(review["elapsed_ms"], int)

    # 잡 조회 응답에도 남지 않아야 한다
    artifacts = list((env["settings"].artifact_dir).glob("*.json"))
    assert artifacts
    for p in artifacts:
        assert API_KEY not in p.read_text(encoding="utf-8")
    art = json.loads(max(artifacts, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8"))
    assert [n["node_id"] for n in art["nodes"]] == ["N-01", "N-02", "N-04", "N-03", "N-11"]


def test_qa_zero_candidates_skips_model(env):
    before = len(env["fake"].calls)
    review = run_query(env["client"], question="qqqzzz", document_ids=[env["doc_id"]])
    assert review["thread_status"] == "insufficient" and review["status_label"] == "근거 부족"
    assert len(env["fake"].calls) == before


def test_model_rate_limit_is_safe_error(env, monkeypatch):
    fake = env["fake"]
    original = FakeOllama.__call__
    monkeypatch.setattr(FakeOllama, "__call__",
                        lambda self, req: httpx.Response(429, headers={"Retry-After": "3"}, text="slow down"))
    try:
        review = run_query(env["client"], question="2024년 경상운영비", document_ids=[env["doc_id"]])
    finally:
        monkeypatch.setattr(FakeOllama, "__call__", original)
    assert fake is env["fake"]
    assert review["thread_status"] == "error" and "slow down" not in json.dumps(review)
    assert review["sources"]  # AI 장애에도 로컬 검색 원문은 제공


# ---------------------------------------------------------------- 로컬 보안
def test_host_and_origin_guard(env):
    c = env["client"]
    assert c.get("/api/health", headers={"host": "evil.example.com"}).status_code == 403
    assert c.get("/api/health", headers={"origin": "http://evil.example.com"}).status_code == 403
    assert c.get("/api/health", headers={"origin": "http://localhost:5173"}).status_code == 200
    assert c.get("/api/health", headers={"origin": "http://127.0.0.1:4173"}).status_code == 200
    assert c.get("/api/health", headers={"origin": "http://127.0.0.1:4174"}).status_code == 403
    assert c.get("/api/health", headers={"origin": "http://127.0.0.1:8765"}).status_code == 200


def test_spa_fallback(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>RA</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", frontend_dist=dist, autodetect_model=False)
    with TestClient(create_app(settings), base_url=BASE) as c:
        assert "RA" in c.get("/").text
        assert "RA" in c.get("/workspace/doc_1").text
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/nothing").status_code == 404
        assert c.get("/api/health").json()["ok"] is True


def test_fallback_tokenizer_matches_particles():
    from reportanchor import textproc

    doc = textproc._fallback("2024년 경상운영비는 4,532백만원이다. Operating Budget")
    query = textproc._fallback("경상운영비 4532")
    assert "4532" in doc and "operating" in doc
    assert all(t in doc for t in query if len(t) >= 2 and t != "경상운영비")
