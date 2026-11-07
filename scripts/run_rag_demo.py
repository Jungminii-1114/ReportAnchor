"""ReportAnchor 자료함·원문 대조·T-01을 연결하는 로컬 웹 서버.

검색은 로컬에서 수행하고, 생성 버튼을 눌렀을 때만 선택한 Ollama 모델을 호출한다.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from dataclasses import asdict
from functools import lru_cache
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.contracts import ContextExpansionConfig, DocumentRef, RetrievalRequest
from rag.nodes.retrieve_evidence import retrieve_evidence
from rag.nodes.draft_answer import DraftAnswerNode
from rag.nodes.get_source_context import (
    build_chunk_lookup, get_source_for_candidate, load_block_lookup,
    reconstruct_chunk_text, group_blocks_by_page,
    get_source_context,
)
from rag.retrieval.bm25 import BM25Retriever, load_chunks
from rag.retrieval.tokenizer import tokenize, TOKENIZER_VERSION
from rag.web.workspace import Workspace, MAX_UPLOAD_BYTES


class Demo:
    def __init__(self, chunks_path: Path, blocks_path: Path, pdf: Path | None,
                 pdf_python: Path | None = None, context_config: ContextExpansionConfig | None = None,
                 draft_node: DraftAnswerNode | None = None):
        self.context_config = context_config or ContextExpansionConfig()
        self.draft_node = draft_node
        self.sessions = OrderedDict()
        self.draft_lock = threading.Lock()
        self.chunks = load_chunks(chunks_path)
        self.lookup = build_chunk_lookup(self.chunks)
        self.blocks = load_block_lookup(blocks_path)
        self.page_blocks = group_blocks_by_page(self.blocks)
        pairs = {(c['doc_id'], c['version_sha256']) for c in self.chunks}
        if len(pairs) != 1:
            raise ValueError('이 데모는 한 문서·한 버전의 청크 파일을 사용합니다.')
        self.doc_id, self.version = next(iter(pairs))
        # 시작 시 모든 청크의 원문 연결을 검사한다. 검색 실패와 데이터 손상을 구분한다.
        for chunk in self.chunks:
            if reconstruct_chunk_text(chunk, self.blocks) != chunk['text']:
                raise ValueError(f"원문 복원 불일치: {chunk['chunk_id']}")
        self.pdf = pdf
        if pdf and hashlib.sha256(pdf.read_bytes()).hexdigest() != self.version:
            raise ValueError('PDF 해시가 청크의 문서 버전과 다릅니다.')
        self.pdf_python = None
        if pdf:
            if pdf_python:
                self.pdf_python = str(pdf_python)
            elif importlib.util.find_spec('pymupdf'):
                self.pdf_python = sys.executable
            elif (ROOT / '.venv/bin/python').exists():
                self.pdf_python = str(ROOT / '.venv/bin/python')
            if not self.pdf_python:
                raise ValueError('PDF 미리보기에는 PyMuPDF가 필요합니다. --pdf-python을 지정하세요.')
            subprocess.run([self.pdf_python, '-c', 'import pymupdf'], check=True,
                           capture_output=True, timeout=20)
        self.retriever = BM25Retriever(self.chunks)
        self.lock = threading.Lock()
        self.index_ref = 'demo-sha256:' + hashlib.sha256(chunks_path.read_bytes()).hexdigest()

    def info(self):
        first = self.chunks[0]
        pages = {b['pdf_page'] for b in self.blocks.values()}
        return dict(title=first.get('document_title', self.doc_id),
                    document_id=self.doc_id, version=self.version,
                    chunk_count=len(self.chunks), block_count=len(self.blocks),
                    page_count=getattr(self, "page_count_override", max(pages)), indexed_count=len(self.retriever.chunks),
                    pdf_available=bool(self.pdf), tokenizer=TOKENIZER_VERSION,
                    pdf_name=self.pdf.name if self.pdf else None,
                    capabilities=['BM25', 'N-01', 'N-02'] + (['N-04', 'N-03', 'T-01'] if self.draft_node else []),
                    context_config=asdict(self.context_config), generation=self.generation_info(),
                    limitations=['모델의 근거 지지 판정이며 실제 정확도는 별도 평가가 필요함', '추출 정확도와 검색 성능은 별도 평가가 필요함'])

    def generation_info(self):
        node = self.draft_node
        return dict(enabled=node is not None, provider=node.provider if node else None,
                    model=node.model if node else None,
                    data_handling=('클라우드 생성 시 질문과 N-02 근거 텍스트를 Ollama Cloud로 전송합니다.'
                                   if node and node.provider == 'cloud'
                                   else '검색과 원문 조회는 로컬에서 수행합니다.'),
                    semantic_verification='not_performed_for_draft')

    def search(self, query: str, top_k: int):
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError('검색어는 4,000자 이하 문자열이어야 합니다.')
        if type(top_k) is not int or not 1 <= top_k <= 20:
            raise ValueError('Top K는 1~20 사이의 정수여야 합니다.')
        started = time.perf_counter()
        request = RetrievalRequest('ui_' + uuid4().hex[:12], query,
                    [DocumentRef(self.doc_id, self.version)], 'bm25-only-development-v1')
        with self.lock:
            result = retrieve_evidence(request, self.retriever,
                                       index_ref=self.index_ref, top_k=top_k)
            tokens = tokenize(query)
        sources = []
        context_result = None
        if result.data:
            context_result = get_source_context(result.data, self.lookup, self.blocks,
                document_scope=request.document_scope, config=self.context_config)
            context_by_chunk = {i.chunk_id: i for i in context_result.data.evidence_items} if context_result.data else {}
            for candidate in result.data.candidates:
                item = context_by_chunk.get(candidate.chunk_id)
                if item is not None:
                    sources.append(self.source_for_candidate(candidate, item, tokens))
        if context_result is not None and context_result.data is not None:
            with self.lock:
                self.sessions[request.request_ref] = dict(
                    question=query, context=context_result, draft=None,
                )
                while len(self.sessions) > 16:
                    self.sessions.popitem(last=False)
        return dict(query=query, query_tokens=tokens, retrieval=asdict(result), sources=sources,
                    source_context=asdict(context_result) if context_result else None,
                    draft=None,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 1))

    def draft(self, request_ref: str):
        if not isinstance(request_ref, str) or not request_ref.strip():
            raise ValueError('검색 요청 ID가 필요합니다.')
        if self.draft_node is None:
            raise ValueError('서버를 --provider 및 --model 옵션으로 다시 실행하세요.')
        # 브라우저가 보낸 원문을 신뢰하지 않고 서버가 보관한 N-02 객체만 사용한다.
        with self.draft_lock:
            with self.lock:
                session = self.sessions.get(request_ref)
            if session is None:
                raise ValueError('검색 결과가 만료되었습니다. 다시 검색하세요.')
            if session['draft'] is not None:
                return dict(request_ref=request_ref, draft=asdict(session['draft']),
                            generation=self.generation_info(), elapsed_ms=0, cached=True)
            started = time.perf_counter()
            result = self.draft_node.run(request_ref, session['question'], session['context'].data)
            if result.status == 'success':
                session['draft'] = result
            return dict(request_ref=request_ref, draft=asdict(result), generation=self.generation_info(),
                        elapsed_ms=round((time.perf_counter() - started) * 1000, 1), cached=False)

    @lru_cache(maxsize=24)
    def preview(self, chunk_id: str, page: int, highlight: bool):
        if not self.pdf:
            raise ValueError('PDF 파일을 연결하지 않았습니다.')
        chunk = self.lookup.get(chunk_id)
        if not chunk or page not in chunk['pdf_pages']:
            raise ValueError('선택한 청크에 포함된 페이지가 아닙니다.')
        locators = [self.blocks[s['block_id']]['source_locator'] for s in chunk['source_spans']
                    if s['pdf_page'] == page] if highlight else []
        payload = dict(pdf=str(self.pdf), page=page, locators=locators)
        completed = subprocess.run(
            [self.pdf_python, str(ROOT / 'scripts/render_rag_preview.py')],
            input=json.dumps(payload).encode(), capture_output=True, timeout=30, check=True)
        return completed.stdout


    def source_for_candidate(self, candidate, item, tokens):
        """서버가 검증한 검색 후보와 N-02 문맥을 화면용 원문 근거로 변환한다."""
        chunk = get_source_for_candidate(candidate, self.lookup)
        text = reconstruct_chunk_text(chunk, self.blocks)
        if text != chunk["text"]:
            raise ValueError("원문 복원 불일치")
        spans = []
        for span in chunk["source_spans"]:
            block = self.blocks[span["block_id"]]
            spans.append({**span, "text": block["text"][span["start_char"]:span["end_char"]],
                          "source_locator": block["source_locator"]})
        return dict(chunk_id=candidate.chunk_id, rank=candidate.rank,
            score=candidate.score_metadata.get("bm25_score", 0), document_id=candidate.document_id,
            document_version=candidate.document_version, title=chunk.get("document_title", ""),
            pages=chunk["pdf_pages"], text=text,
            matched_tokens=sorted(set(tokens) & set(tokenize(chunk["search_text"]))),
            reconstruction_match=True, verification_status=chunk.get("verification_status", "unverified"),
            source_spans=spans, context_text=item.context_text, context_source_spans=spans,
            evidence_refs=[item.chunk_id], context_chunk_ids=[item.chunk_id],
            context_verification_status=chunk.get("verification_status", "unverified"),
            page_block_count=sum(len(self.page_blocks.get((self.doc_id, self.version, page), []))
                                 for page in chunk["pdf_pages"]))
    @lru_cache(maxsize=24)
    def page(self, page: int):
        """검색 전에도 원본 PDF의 물리 페이지를 볼 수 있다."""
        if not self.pdf or type(page) is not int or not 1 <= page <= self.info()["page_count"]:
            raise ValueError("표시할 PDF 페이지를 확인하세요.")
        completed = subprocess.run([self.pdf_python, str(ROOT / "scripts/render_rag_preview.py")],
            input=json.dumps(dict(pdf=str(self.pdf), page=page, locators=[])).encode(),
            capture_output=True, timeout=30, check=True)
        return completed.stdout


def make_handler(demo):
    manager = demo if isinstance(demo, Workspace) else None

    class Handler(BaseHTTPRequestHandler):
        def reply(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, value, status=200):
            self.reply(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
                       "application/json; charset=utf-8", status)

        def local_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}",
                                                  f"localhost:{self.server.server_port}"}

        def do_GET(self):
            if not self.local_host():
                self.send_json({"error": "허용되지 않은 호스트입니다."}, 403)
                return
            parsed = urlsplit(self.path)
            args = parse_qs(parsed.query)
            try:
                if parsed.path == "/api/info":
                    self.send_json(manager.info(args.get("document_id", [None])[0]) if manager else demo.info())
                elif parsed.path == "/api/documents" and manager:
                    self.send_json(manager.list_documents())
                elif parsed.path == "/api/jobs" and manager:
                    self.send_json(manager.job(args["id"][0]))
                elif parsed.path == "/api/preview":
                    if manager:
                        image = manager.preview(args["document_id"][0], int(args["page"][0]),
                            args.get("chunk_id", [None])[0], args.get("highlight", ["1"])[0] == "1")
                    else:
                        image = demo.preview(args["chunk_id"][0], int(args["page"][0]),
                            args.get("highlight", ["1"])[0] == "1")
                    self.reply(image, "image/png")
                elif parsed.path in {"/", "/app.js", "/style.css"}:
                    name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[parsed.path]
                    mime = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[name]
                    self.reply((ROOT / "demo" / name).read_bytes(), mime + "; charset=utf-8")
                else:
                    self.send_json({"error": "경로를 찾을 수 없습니다."}, 404)
            except (ValueError, KeyError, TypeError) as error:
                self.send_json({"error": str(error)}, 400)
            except (subprocess.SubprocessError, OSError):
                self.send_json({"error": "원문을 표시하지 못했습니다. 서버 실행 창을 확인하세요."}, 500)

        def do_POST(self):
            if not self.local_host() or self.headers.get("Origin") not in {
                None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"
            }:
                self.send_json({"error": "다른 사이트에서의 요청은 허용하지 않습니다."}, 403)
                return
            parsed = urlsplit(self.path)
            allowed = {"/api/search", "/api/draft"}
            if manager:
                allowed |= {"/api/documents/upload", "/api/documents/update", "/api/model", "/api/qa"}
            if parsed.path not in allowed:
                self.send_json({"error": "경로를 찾을 수 없습니다."}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                limit = MAX_UPLOAD_BYTES if parsed.path == "/api/documents/upload" else 32000
                if not 0 < length <= limit:
                    self.send_json({"error": "업로드는 30MB 이하, 질문 요청은 32KB 이하로 제한됩니다."}, 413)
                    return
                content = self.rfile.read(length)
                if len(content) != length:
                    raise ValueError("파일 전송이 완료되지 않았습니다.")
                if parsed.path == "/api/documents/upload":
                    filename = parse_qs(parsed.query).get("filename", [""])[0]
                    self.send_json(manager.upload(filename, content), 202)
                    return
                body = json.loads(content)
                if not isinstance(body, dict):
                    raise ValueError("JSON 객체가 필요합니다.")
                if parsed.path == "/api/search":
                    result = (manager.search(body.get("query"), body.get("top_k", 3), body.get("document_ids"))
                              if manager else demo.search(body.get("query"), body.get("top_k", 3)))
                    self.send_json(result)
                elif parsed.path == "/api/draft":
                    self.send_json(demo.draft(body.get("request_ref")))
                elif parsed.path == "/api/model":
                    self.send_json(manager.configure_model(body.get("model"), body.get("provider", "local")))
                elif parsed.path == "/api/documents/update":
                    if "title" not in body and "active" not in body:
                        raise ValueError("변경할 자료 이름이나 상태가 필요합니다.")
                    self.send_json(manager.update_document(body.get("document_id"), title=body.get("title"), active=body.get("active")))
                elif parsed.path == "/api/qa":
                    self.send_json(manager.qa(body.get("query"), body.get("top_k", 3),
                        body.get("document_ids"), body.get("request_ref")), 202)
            except (ValueError, TypeError) as error:
                self.send_json({"error": str(error)}, 400)
            except Exception:
                import traceback
                traceback.print_exc()
                self.send_json({"error": "요청을 처리하지 못했습니다. 서버 실행 창을 확인하세요."}, 500)
    return Handler


def find_pdf_python(requested=None):
    choices = [str(requested)] if requested else ([sys.executable] if importlib.util.find_spec("pymupdf") else [])
    if not requested and (ROOT / ".venv/bin/python").exists():
        choices.append(str(ROOT / ".venv/bin/python"))
    for candidate in choices:
        try:
            subprocess.run([candidate, "-c", "import pymupdf"], check=True, capture_output=True, timeout=20)
            return candidate
        except (OSError, subprocess.SubprocessError):
            continue
    if requested:
        raise ValueError("--pdf-python 환경에서 PyMuPDF를 불러오지 못했습니다.")
    return None


def main():
    parser = argparse.ArgumentParser(description="ReportAnchor 자료함·원문 대조·T-01 로컬 웹")
    parser.add_argument("--chunks", type=Path)
    parser.add_argument("--blocks", type=Path)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--pdf-python", type=Path)
    parser.add_argument("--empty", action="store_true", help="기본 문서 없이 자료함으로 시작")
    parser.add_argument("--library-dir", type=Path, default=ROOT / "data/local_workspace")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--before-chars", type=int, default=200)
    parser.add_argument("--after-chars", type=int, default=200)
    parser.add_argument("--max-total-chars", type=int, default=12000)
    parser.add_argument("--provider", choices=("local", "cloud"), default="local")
    parser.add_argument("--model", help="생략하면 웹의 모델 설정에서 적용할 수 있습니다.")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if bool(args.chunks) != bool(args.blocks):
        parser.error("--chunks와 --blocks는 함께 지정하세요.")
    pdf_python = find_pdf_python(args.pdf_python)
    context = ContextExpansionConfig(args.before_chars, args.after_chars, args.max_total_chars)
    generator = DraftAnswerNode(args.model, "https://ollama.com" if args.provider == "cloud" else "http://localhost:11434",
        provider=args.provider, api_key=os.environ.get("OLLAMA_API_KEY") if args.provider == "cloud" else None,
        timeout=args.timeout) if args.model else None
    if args.chunks is None and not args.empty:
        default_chunks = ROOT / "data/processed/rag/kistep_2025_plan_0530d100e085/3382f591c928/chunks.jsonl"
        default_blocks = ROOT / "data/processed/pymupdf_demo/kistep_2025_plan_0530d100e085/blocks.jsonl"
        if default_chunks.exists() and default_blocks.exists():
            args.chunks, args.blocks = default_chunks, default_blocks
            if args.pdf is None:
                default_pdf = ROOT / "data/raw/kistep_2025_plan.pdf"
                args.pdf = default_pdf if default_pdf.exists() else None
    print("자료함과 검색 색인을 준비합니다…", flush=True)
    seed = Demo(args.chunks.resolve(), args.blocks.resolve(), args.pdf.resolve() if args.pdf else None,
                Path(pdf_python) if pdf_python else None, context, generator) if args.chunks and not args.empty else None
    workspace = Workspace(args.library_dir, Demo, seed=seed, pdf_python=pdf_python,
                          context_config=context, draft_node=generator)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(workspace))
    server.daemon_threads = True
    print(f"ReportAnchor: http://127.0.0.1:{server.server_port} (종료: Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        workspace.close()


if __name__ == '__main__':
    main()
