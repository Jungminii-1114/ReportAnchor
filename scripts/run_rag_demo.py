"""ReportAnchor N-01/N-02/N-04를 연결하는 로컬 웹 데모 서버.

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
                    page_count=max(pages), indexed_count=len(self.retriever.chunks),
                    pdf_available=bool(self.pdf), tokenizer=TOKENIZER_VERSION,
                    pdf_name=self.pdf.name if self.pdf else None,
                    capabilities=['BM25', 'N-01', 'N-02'] + (['N-04'] if self.draft_node else []),
                    context_config=asdict(self.context_config), generation=self.generation_info(),
                    limitations=['N-03 의미 검증은 미구현', '추출 정확도와 검색 성능은 별도 평가가 필요함'])

    def generation_info(self):
        node = self.draft_node
        return dict(enabled=node is not None, provider=node.provider if node else None,
                    model=node.model if node else None,
                    data_handling=('클라우드 생성 시 질문과 N-02 근거 텍스트를 Ollama Cloud로 전송합니다.'
                                   if node and node.provider == 'cloud'
                                   else '검색과 원문 조회는 로컬에서 수행합니다.'),
                    semantic_verification='not_implemented')

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
                if item is None:
                    continue
                chunk = get_source_for_candidate(candidate, self.lookup)
                text = reconstruct_chunk_text(chunk, self.blocks)
                if text != chunk['text']:
                    raise ValueError('원문 복원 불일치')
                spans = []
                for span in chunk['source_spans']:
                    block = self.blocks[span['block_id']]
                    spans.append({**span, 'text': block['text'][span['start_char']:span['end_char']],
                                  'source_locator': block['source_locator']})
                sources.append(dict(
                    chunk_id=candidate.chunk_id, rank=candidate.rank,
                    score=candidate.score_metadata['bm25_score'],
                    document_id=candidate.document_id, document_version=candidate.document_version,
                    title=chunk.get('document_title', ''), pages=chunk['pdf_pages'], text=text,
                    matched_tokens=sorted(set(tokens) & set(tokenize(chunk['search_text']))),
                    reconstruction_match=True, verification_status=chunk.get('verification_status', 'unverified'),
                    source_spans=spans, context_text=item.context_text,
                    context_source_spans=spans, evidence_refs=[item.chunk_id],
                    context_chunk_ids=[item.chunk_id],
                    context_verification_status=chunk.get('verification_status', 'unverified'),
                    page_block_count=sum(len(self.page_blocks.get((self.doc_id, self.version, p), [])) for p in chunk['pdf_pages']),
                ))
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


def make_handler(demo: Demo):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy',
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data, status=200):
            self.reply(json.dumps(data, ensure_ascii=False, allow_nan=False).encode(),
                       'application/json; charset=utf-8', status)

        def local_host(self):
            # 브라우저의 다른 사이트에서 로컬 데이터에 요청하는 것을 차단한다.
            port = self.server.server_port
            return self.headers.get('Host') in {f'127.0.0.1:{port}', f'localhost:{port}'}

        def do_GET(self):
            if not self.local_host():
                self.send_json({'error': '허용되지 않은 호스트입니다.'}, 403)
                return
            parsed = urlsplit(self.path)
            try:
                if parsed.path == '/api/info':
                    self.send_json(demo.info())
                elif parsed.path == '/api/preview':
                    args = parse_qs(parsed.query)
                    image = demo.preview(args['chunk_id'][0], int(args['page'][0]),
                                         args.get('highlight', ['1'])[0] == '1')
                    self.reply(image, 'image/png')
                elif parsed.path in {'/', '/app.js', '/style.css'}:
                    name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[parsed.path]
                    mime = {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name]
                    self.reply((ROOT / 'demo' / name).read_bytes(), mime + '; charset=utf-8')
                else:
                    self.send_json({'error': '경로를 찾을 수 없습니다.'}, 404)
            except (ValueError, KeyError) as error:
                self.send_json({'error': str(error)}, 400)
            except (subprocess.SubprocessError, OSError):
                self.send_json({'error': 'PDF 미리보기를 만들지 못했습니다. 실행 터미널을 확인하세요.'}, 500)

        def do_POST(self):
            port = self.server.server_port
            if not self.local_host() or self.headers.get('Origin') not in {
                None, f'http://127.0.0.1:{port}', f'http://localhost:{port}'
            }:
                self.send_json({'error': '다른 사이트에서의 요청은 허용하지 않습니다.'}, 403)
                return
            if self.path not in {'/api/search', '/api/draft'}:
                self.send_json({'error': '경로를 찾을 수 없습니다.'}, 404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 32000:
                    raise ValueError('요청 크기가 올바르지 않습니다.')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('JSON 객체가 필요합니다.')
                if self.path == '/api/search':
                    self.send_json(demo.search(body.get('query'), body.get('top_k', 3)))
                else:
                    self.send_json(demo.draft(body.get('request_ref')))
            except (ValueError, TypeError) as error:
                self.send_json({'error': str(error)}, 400)
            except Exception:
                import traceback
                traceback.print_exc()
                self.send_json({'error': '요청 처리 중 오류가 발생했습니다. 실행 터미널을 확인하세요.'}, 500)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chunks', type=Path, required=True)
    parser.add_argument('--blocks', type=Path, required=True)
    parser.add_argument('--pdf', type=Path)
    parser.add_argument('--pdf-python', type=Path, help='PyMuPDF가 설치된 Python (별도 환경도 가능)')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--before-chars', type=int, default=200)
    parser.add_argument('--after-chars', type=int, default=200)
    parser.add_argument('--max-total-chars', type=int, default=12000)
    parser.add_argument('--provider', choices=['local', 'cloud'], default='local')
    parser.add_argument('--model', help='Ollama 모델명. 생략하면 검색·원문 조회만 사용')
    parser.add_argument('--timeout', type=float, default=120.0)
    args = parser.parse_args()
    if args.provider == 'local' and args.model and args.model.endswith(('-cloud', ':cloud')):
        parser.error('로컬 모드에는 다운로드한 로컬 모델을 사용하세요. 클라우드는 --provider cloud로 선택합니다.')
    draft_node = DraftAnswerNode(
        model=args.model, provider=args.provider, timeout=args.timeout,
        base_url='https://ollama.com' if args.provider == 'cloud' else 'http://localhost:11434',
        api_key=os.environ.get('OLLAMA_API_KEY') if args.provider == 'cloud' else None,
    ) if args.model else None
    print('청크·원문 검사 및 BM25 인덱스를 준비합니다…', flush=True)
    demo = Demo(args.chunks.expanduser().resolve(), args.blocks.expanduser().resolve(),
                args.pdf.expanduser().resolve() if args.pdf else None,
                args.pdf_python.expanduser().resolve() if args.pdf_python else None,
                ContextExpansionConfig(args.before_chars, args.after_chars, args.max_total_chars),
                draft_node=draft_node)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(demo))
    server.daemon_threads = True
    print(f'ReportAnchor: http://127.0.0.1:{server.server_port} (종료: Ctrl+C)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
