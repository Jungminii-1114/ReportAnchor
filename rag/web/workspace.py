"""문서 목록·업로드·범위 선택·T-01 비동기 실행을 관리하는 로컬 어댑터."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from rag.contracts import ContextExpansionConfig, DocumentRef, RetrievalRequest, RoutedTask
from rag.nodes.draft_answer import DraftAnswerNode
from rag.nodes.get_source_context import build_chunk_lookup, get_source_context, load_block_lookup
from rag.nodes.retrieve_evidence import retrieve_evidence
from rag.nodes.verify_grounding import _validate_client_config
from rag.retrieval.bm25 import BM25Retriever
from rag.retrieval.tokenizer import tokenize
from rag.workflows.document_qa import DocumentQAWorkflow

ROOT = Path(__file__).resolve().parents[2]
MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def document_key(doc_id, version):
    return "d_" + hashlib.sha256((doc_id + ":" + version).encode()).hexdigest()[:24]


class Workspace:
    def __init__(self, directory: Path, factory, *, seed=None, seed_paths=None,
                 pdf_python=None, context_config=None, draft_node=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.factory = factory
        self.pdf_python = str(pdf_python) if pdf_python else None
        self.context_config = context_config or ContextExpansionConfig()
        self.draft_node = draft_node
        self.lock = threading.RLock()
        self.model_lock = threading.Lock()
        self.upload_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-ingest")
        self.qa_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="document-qa")
        self.jobs = OrderedDict()
        self.demos = {}
        self.indexes = OrderedDict()
        self.sessions = OrderedDict()
        catalog = self.directory / "catalog.json"
        if catalog.exists():
            value = json.loads(catalog.read_text(encoding="utf-8"))
            if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                raise ValueError("자료함 목록 파일의 형식을 확인하세요.")
            self.records = {row["id"]: row for row in value}
            for key, row in self.records.items():
                if not re.fullmatch(r"d_[a-f0-9]{24}", key) or row.get("kind") not in ("upload", "seed"):
                    raise ValueError("자료함 식별자 형식을 확인하세요.")
                if row["kind"] == "upload":
                    for field in ("chunks", "blocks", "pdf"):
                        self._owned_path(row[field])
        else:
            self.records = {}
        # Seed paths come from the CLI, never from the browser or catalog.
        self.seed_paths = seed_paths
        self.seed_key = None
        if seed is not None:
            key = document_key(seed.doc_id, seed.version)
            self.seed_key = key
            old = self.records.get(key, {})
            details = seed.info()
            self.records[key] = dict(id=key, kind="seed", title=old.get("title", details["pdf_name"] or details["title"]),
                active=old.get("active", True), doc_id=seed.doc_id, version=seed.version,
                page_count=details["page_count"], chunk_count=details["chunk_count"],
                block_count=details["block_count"], created_at=old.get("created_at", self._now()))
            self.demos[key] = seed
        self._save()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _owned_path(self, relative):
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("자료함 경로가 올바르지 않습니다.")
        path = (self.directory / relative).resolve()
        if not path.is_relative_to(self.directory) or path == self.directory:
            raise ValueError("자료함 바깥의 파일은 사용할 수 없습니다.")
        return path

    def _save(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory, delete=False) as stream:
            path = Path(stream.name)
            json.dump(list(self.records.values()), stream, ensure_ascii=False, indent=2)
        os.replace(path, self.directory / "catalog.json")

    def close(self):
        self.upload_pool.shutdown(wait=True, cancel_futures=True)
        self.qa_pool.shutdown(wait=True, cancel_futures=True)

    def generation_info(self):
        node = self.draft_node
        return dict(enabled=node is not None, model=node.model if node else None,
                    provider=node.provider if node else "local", semantic_verification="available_in_t01",
                    data_handling=("클라우드 모드: 질문과 선택한 원문을 Ollama Cloud로 전송합니다."
                        if node and node.provider == "cloud" else "자료 저장·검색·기본 모델 처리는 로컬 환경을 사용합니다."))

    def list_documents(self):
        with self.lock:
            visible = [self._public(row) for key, row in self.records.items()
                       if row["kind"] == "upload" or key == self.seed_key]
        return dict(documents=visible, generation=self.generation_info(),
                    upload=dict(enabled=bool(self.pdf_python), max_bytes=MAX_UPLOAD_BYTES, max_pages=300),
                    context_config=asdict(self.context_config))

    @staticmethod
    def _public(row):
        fields = ("id", "kind", "title", "active", "doc_id", "version", "page_count", "chunk_count", "block_count", "created_at", "no_native_text_pages")
        return {key: deepcopy(row[key]) for key in fields if key in row}

    def _record(self, key, *, active=True):
        if not isinstance(key, str) or key not in self.records:
            raise ValueError("자료를 찾을 수 없습니다.")
        row = self.records[key]
        if row["kind"] == "seed" and key != self.seed_key:
            raise ValueError("현재 서버에 연결되지 않은 기본 자료입니다.")
        if active and not row["active"]:
            raise ValueError("목록에서 제외된 자료입니다. 복원 후 사용하세요.")
        return row

    def update_document(self, key, *, title=None, active=None):
        with self.lock:
            row = self._record(key, active=False)
            if title is not None:
                if not isinstance(title, str) or not title.strip() or len(title) > 120:
                    raise ValueError("자료 이름은 1~120자로 입력하세요.")
                row["title"] = title.strip()
            if active is not None:
                if type(active) is not bool:
                    raise ValueError("자료 상태가 올바르지 않습니다.")
                row["active"] = active
            self._save()
            return self._public(row)

    def demo(self, key):
        with self.lock:
            row = self._record(key)
            if key not in self.demos:
                self.demos[key] = self.factory(self._owned_path(row["chunks"]), self._owned_path(row["blocks"]),
                    self._owned_path(row["pdf"]), pdf_python=Path(self.pdf_python) if self.pdf_python else None,
                    context_config=self.context_config, draft_node=self.draft_node)
                self.demos[key].page_count_override = row["page_count"]
            self.demos[key].draft_node = self.draft_node
            return self.demos[key]

    def info(self, key=None):
        with self.lock:
            if key is None:
                key = next((row["id"] for row in self.list_documents()["documents"] if row["active"]), None)
            if key is None:
                return dict(document_key=None, title="나의 자료함", page_count=0, chunk_count=0,
                    block_count=0, pdf_available=False, generation=self.generation_info(),
                    context_config=asdict(self.context_config))
            row = self._record(key)
            result = self.demo(key).info()
            result.update(document_key=key, title=row["title"], pdf_name=row["title"], generation=self.generation_info())
            return result

    def preview(self, key, page, chunk_id=None, highlight=True):
        demo = self.demo(key)
        if chunk_id:
            return demo.preview(chunk_id, page, highlight)
        return demo.page(page)

    def configure_model(self, model, provider="local"):
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("설치된 Ollama 모델 이름을 입력하세요.")
        if provider not in ("local", "cloud"):
            raise ValueError("모델 실행 방식을 확인하세요.")
        node = DraftAnswerNode(model.strip(), "https://ollama.com" if provider == "cloud" else "http://localhost:11434",
            provider=provider, api_key=os.environ.get("OLLAMA_API_KEY") if provider == "cloud" else None)
        try:
            _validate_client_config(node.client)
        except (TypeError, ValueError):
            raise ValueError("모델 설정을 확인하세요. Cloud는 서버의 OLLAMA_API_KEY가 필요합니다.") from None
        with self.model_lock, self.lock:
            self.draft_node = node
            self.sessions.clear()
            for demo in self.demos.values():
                demo.draft_node = node
                demo.sessions.clear()
        return self.generation_info()

    def _new_job(self, kind):
        with self.lock:
            running = sum(job["status"] in ("queued", "running") for job in self.jobs.values())
            if running >= 4:
                raise ValueError("처리 중인 작업이 많습니다. 현재 작업이 끝난 뒤 다시 시도하세요.")
            while len(self.jobs) >= 32:
                done = next((key for key, job in self.jobs.items() if job["status"] not in ("queued", "running")), None)
                if done is None:
                    break
                del self.jobs[done]
            key = uuid4().hex
            self.jobs[key] = dict(id=key, kind=kind, status="queued", stage="대기 중", started_at=time.monotonic())
            return key

    def job(self, key):
        with self.lock:
            if key not in self.jobs:
                raise ValueError("작업이 만료되었습니다. 다시 실행하세요.")
            result = deepcopy(self.jobs[key])
            result["elapsed_ms"] = round((time.monotonic() - result.pop("started_at")) * 1000)
            return result

    def _update_job(self, key, **kwargs):
        with self.lock:
            self.jobs[key].update(kwargs)

    def upload(self, filename, content):
        if not self.pdf_python:
            raise ValueError("PDF 업로드에는 PyMuPDF 환경이 필요합니다. --pdf-python을 지정하세요.")
        if (not isinstance(filename, str) or not filename.lower().endswith(".pdf")
                or any(char in filename for char in ("/", "\\", "\x00")) or len(filename) > 180):
            raise ValueError("PDF 파일 이름을 확인하세요.")
        if not 0 < len(content) <= MAX_UPLOAD_BYTES or b"%PDF-" not in content[:1024]:
            raise ValueError("30MB 이하의 올바른 PDF 파일을 사용하세요.")
        version = hashlib.sha256(content).hexdigest()
        key = self._new_job("upload")
        with self.lock:
            existing = next((row for row in self.records.values() if row.get("version") == version
                             and (row["kind"] == "upload" or row["id"] == self.seed_key)), None)
            if existing:
                existing["active"] = True
                self._save()
                self._update_job(key, status="completed", stage="이미 등록된 자료", result=self._public(existing))
                return self.job(key)
        self.upload_pool.submit(self._upload_worker, key, filename, content, version)
        return self.job(key)

    def _upload_worker(self, key, filename, content, version):
        temporary = Path(tempfile.mkdtemp(prefix=".upload-", dir=self.directory))
        try:
            self._update_job(key, status="running", stage="PDF 텍스트 추출·청킹 중")
            pdf = temporary / "document.pdf"
            pdf.write_bytes(content)
            folder = self.directory / ("pdf_" + version)
            process = subprocess.run([self.pdf_python, str(ROOT / "scripts/ingest_uploaded_pdf.py"),
                "--pdf", str(pdf), "--out", str(temporary), "--final-pdf", str(folder / "document.pdf")],
                capture_output=True, timeout=180)
            try:
                report = json.loads(process.stdout)
            except (ValueError, UnicodeError):
                raise ValueError("PDF 처리에 실패했습니다. PyMuPDF 환경과 파일을 확인하세요.") from None
            if process.returncode or report.get("status") != "success":
                raise ValueError(report.get("error", "PDF를 처리하지 못했습니다."))
            data = report["data"]
            if data.get("version") != version:
                raise ValueError("업로드한 PDF와 추출 결과의 버전이 일치하지 않습니다.")
            self._update_job(key, stage="검색 색인 준비 중")
            doc_key = document_key(data["doc_id"], data["version"])
            with self.lock:
                if doc_key in self.records:
                    self.records[doc_key]["active"] = True
                    self._save()
                    self._update_job(key, status="completed", stage="이미 등록된 자료", result=self._public(self.records[doc_key]))
                    return
                if folder.exists():
                    raise ValueError("이전 업로드 파일이 남아 있습니다. 자료함 저장 경로를 확인하세요.")
                os.replace(temporary, folder)
                row = dict(id=doc_key, kind="upload", title=filename, active=True,
                    pdf=str((folder / "document.pdf").relative_to(self.directory)),
                    chunks=str((folder / data["chunks"]).relative_to(self.directory)),
                    blocks=str((folder / data["blocks"]).relative_to(self.directory)),
                    created_at=self._now(), doc_id=data["doc_id"], version=version,
                    page_count=data["page_count"], chunk_count=data["chunk_count"], block_count=data["block_count"],
                    no_native_text_pages=data["no_native_text_pages"])
                self.records[doc_key] = row
                try:
                    self.demo(doc_key)
                except Exception:
                    del self.records[doc_key]
                    shutil.rmtree(folder)
                    raise ValueError("텍스트는 추출했지만 검색 색인을 만들지 못했습니다.") from None
                self._save()
                self._update_job(key, status="completed", stage="자료 추가 완료", result=self._public(row))
        except (ValueError, subprocess.SubprocessError, OSError) as error:
            message = str(error) if isinstance(error, ValueError) else "PDF 처리 시간이 초과되었거나 저장하지 못했습니다."
            self._update_job(key, status="error", stage="추가 실패", error=message)
        except Exception:
            self._update_job(key, status="error", stage="추가 실패", error="자료를 추가하지 못했습니다.")
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _inputs(self, ids):
        if not isinstance(ids, list) or not ids or len(ids) > 20 or any(not isinstance(key, str) for key in ids):
            raise ValueError("검색할 자료를 1~20개 선택하세요.")
        ids = list(dict.fromkeys(ids))
        with self.lock:
            demos = [(key, self.demo(key)) for key in ids]
            cache_key = tuple(sorted(ids))
            if cache_key not in self.indexes:
                chunks = [chunk for _, demo in demos for chunk in demo.chunks]
                self.indexes[cache_key] = BM25Retriever(chunks)
                while len(self.indexes) > 4:
                    self.indexes.popitem(last=False)
            retriever = self.indexes[cache_key]
        chunks = {chunk_id: chunk for _, demo in demos for chunk_id, chunk in demo.lookup.items()}
        blocks = {block_id: block for _, demo in demos for block_id, block in demo.blocks.items()}
        scope = [DocumentRef(demo.doc_id, demo.version) for _, demo in demos]
        return demos, retriever, chunks, blocks, scope

    @staticmethod
    def _validate_query(query, top_k):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError("질문을 1~4,000자로 입력하세요.")
        if type(top_k) is not int or not 1 <= top_k <= 10:
            raise ValueError("검색 결과 수는 1~10개로 선택하세요.")

    def _sources(self, demos, retrieval, context, tokens):
        if not retrieval or not retrieval.data or not context or not context.data:
            return []
        items = {item.chunk_id: item for item in context.data.evidence_items}
        by_identity = {(demo.doc_id, demo.version): (key, demo) for key, demo in demos}
        result = []
        for candidate in retrieval.data.candidates:
            item = items.get(candidate.chunk_id)
            if item is None:
                continue
            key, demo = by_identity[(candidate.document_id, candidate.document_version)]
            source = demo.source_for_candidate(candidate, item, tokens)
            source.update(document_key=key, title=self.records[key]["title"])
            result.append(source)
        return result

    def search(self, query, top_k, ids):
        self._validate_query(query, top_k)
        started = time.monotonic()
        demos, retriever, chunks, blocks, scope = self._inputs(ids)
        request = RetrievalRequest("ui_" + uuid4().hex[:16], query, scope, "bm25-baseline-v1")
        result = retrieve_evidence(request, retriever, top_k=top_k)
        context = get_source_context(result.data, chunks, blocks, document_scope=scope,
                    config=self.context_config) if result.status != "error" else None
        tokens = tokenize(query)
        with self.lock:
            if context is not None and context.data is not None:
                self.sessions[request.request_ref] = dict(question=query, context=context, draft=None)
                while len(self.sessions) > 16:
                    self.sessions.popitem(last=False)
        return dict(kind="search", query=query, request_ref=request.request_ref, document_ids=ids,
            query_tokens=tokens, retrieval=asdict(result), source_context=asdict(context) if context else None,
            sources=self._sources(demos, result, context, tokens), draft=None,
            elapsed_ms=round((time.monotonic() - started) * 1000), generation=self.generation_info())

    def draft(self, request_ref):
        with self.model_lock:
            if self.draft_node is None:
                raise ValueError("먼저 모델을 설정하세요.")
            with self.lock:
                session = self.sessions.get(request_ref)
            if session is None:
                raise ValueError("검색 결과가 만료되었습니다. 다시 검색하세요.")
            if session["draft"] is None:
                result = self.draft_node.run(request_ref, session["question"], session["context"].data)
                if result.status == "success":
                    session["draft"] = result
            else:
                result = session["draft"]
            return dict(request_ref=request_ref, draft=asdict(result), generation=self.generation_info())

    def qa(self, query, top_k, ids, request_ref):
        self._validate_query(query, top_k)
        if not isinstance(request_ref, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request_ref):
            raise ValueError("요청 식별자가 올바르지 않습니다.")
        self._inputs(ids)  # Reject archived/unknown IDs before enqueueing.
        if self.draft_node is None:
            raise ValueError("왼쪽 모델 설정에서 Ollama 모델을 적용하세요.")
        fingerprint = hashlib.sha256(json.dumps([query, top_k, ids], ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            existing = next((job for job in self.jobs.values() if job.get("request_ref") == request_ref), None)
            if existing:
                if existing.get("fingerprint") != fingerprint:
                    raise ValueError("같은 요청 ID로 다른 질문을 실행할 수 없습니다.")
                return self.job(existing["id"])
            key = self._new_job("qa")
            self.jobs[key].update(request_ref=request_ref, fingerprint=fingerprint)
        self.qa_pool.submit(self._qa_worker, key, query, top_k, list(ids), request_ref)
        return self.job(key)

    def _qa_worker(self, key, query, top_k, ids, request_ref):
        started = time.monotonic()
        try:
            with self.model_lock:
                self._update_job(key, status="running", stage="선택한 자료 검색 중")
                demos, retriever, chunks, blocks, scope = self._inputs(ids)
                workflow = DocumentQAWorkflow(retriever, chunks, blocks, self.draft_node, top_k=top_k,
                    context_profiles={"fixed-context-v1": self.context_config})
                task = RoutedTask(request_ref, query, scope, "bm25-baseline-v1", "fixed-context-v1")
                state = {"task": task, "revision_attempted": False}
                labels = {"validate_input": "입력 확인 완료", "N-01": "검색 완료 · 원문 문맥 조회 중",
                          "N-02": "원문 연결 완료 · 답변 초안 작성 중", "N-04": "주장 생성 완료 · 원문 대조 중",
                          "N-03": "판정 완료 · 필요 시 1회 보정 중", "assemble_result": "결과 정리 완료"}
                for event in workflow.graph.stream(state, config={"recursion_limit": 16}, stream_mode="updates"):
                    for node, update in event.items():
                        if update is not None:
                            state.update(update)
                        self._update_job(key, stage=labels.get(node, "처리 중"))
                tokens = tokenize(query)
                response = dict(kind="workflow", query=query, request_ref=request_ref, document_ids=ids,
                    query_tokens=tokens, workflow_result=asdict(state["result"]),
                    sources=self._sources(demos, state.get("n01"), state.get("n02"), tokens),
                    generation=self.generation_info(), elapsed_ms=round((time.monotonic() - started) * 1000),
                    trace={name: asdict(state[name]) for name in
                        ("n01", "n02", "n04", "n03", "n04_revision", "n03_recheck") if name in state})
                self._update_job(key, status="completed", stage="검토 완료", result=response)
        except (TypeError, ValueError):
            self._update_job(key, status="error", stage="실행 실패", error="선택한 자료와 모델 설정을 확인하세요.")
        except Exception:
            self._update_job(key, status="error", stage="실행 실패", error="검토를 완료하지 못했습니다. 서버 실행 창을 확인하세요.")
