"""FastAPI 앱: 127.0.0.1 전용 REST API + (있으면) frontend/dist 정적 SPA 서빙."""

from __future__ import annotations

import json
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from . import VERSION
from .config import DEV_UI_ORIGINS, MAX_UPLOAD_BYTES, Settings
from .jobs import JobQueue, UploadService, document_meta
from .llm import ModelManager
from .parsing import render_page_png
from .search import SearchIndex
from .store import Store
from .workflow import QueryRunner, lookup_chunk

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_SAFE_NAME_RE = re.compile(r"[\x00-\x1f<>:\"|?*]")


# ---------------------------------------------------------------- 요청 모델
class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    active: bool | None = None


class ModelRequest(BaseModel):
    provider: Literal["local", "cloud"]
    model: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=300)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] = Field(min_length=1, max_length=50)
    mode: Literal["qa", "search"] = "qa"
    top_k: int = Field(default=5, ge=1, le=20)
    request_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")


# ---------------------------------------------------------------- 앱 상태
class AppState:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None):
        settings.ensure_dirs()
        self.settings = settings
        self.store = Store(settings.db_path)
        self.store.init()
        self.models = ModelManager(settings, self.store, transport)
        self.index = SearchIndex(self.store)
        self.queue = JobQueue(self.store)
        self.uploads = UploadService(settings, self.store, self.queue)
        self.queries = QueryRunner(self.store, self.index, self.models, str(settings.artifact_dir))
        self.queue.register("upload", self.uploads.handle, workers=1)
        self.queue.register("query", self.queries.run, workers=settings.query_workers)

    def start(self) -> None:
        self.models.initialize()
        self.queue.start()

    def stop(self) -> None:
        self.queue.stop()
        self.uploads.shutdown()


def _hostname(value: str) -> str | None:
    try:
        return urlsplit(f"//{value}").hostname
    except ValueError:
        return None


def _safe_filename(name: str | None) -> str:
    base = re.split(r"[\\/]", name or "")[-1].strip()
    return _SAFE_NAME_RE.sub("_", base)[:200]


def create_app(settings: Settings | None = None, *, ollama_transport: httpx.BaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state = AppState(settings, ollama_transport)
    allowed_origins = {
        f"http://127.0.0.1:{settings.port}", f"http://localhost:{settings.port}", *DEV_UI_ORIGINS,
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.start()
        try:
            yield
        finally:
            state.stop()

    app = FastAPI(title="ReportAnchor Local Daemon", version=VERSION, lifespan=lifespan)
    app.state.ra = state
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(DEV_UI_ORIGINS),
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def loopback_guard(request: Request, call_next):
        # DNS 재바인딩·다른 웹사이트의 요청 위조를 막기 위해 Host와 Origin을 루프백으로 제한한다.
        host = _hostname(request.headers.get("host", ""))
        if host not in LOOPBACK_HOSTS:
            return JSONResponse({"detail": "허용되지 않은 Host입니다."}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, status_code=403)
        return await call_next(request)

    # ------------------------------------------------------------ 상태·모델
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        d = state.models.describe()
        return {"ok": True, "version": VERSION, "ollama_mode": d["provider"], "model": d["model"],
                "model_configured": d["enabled"]}

    @app.post("/api/model")
    def set_model(body: ModelRequest) -> dict[str, Any]:
        try:
            state.models.set(body.provider, body.model, body.base_url)
        except PermissionError as exc:
            raise HTTPException(400, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, f"잘못된 AI 서버 주소입니다: {exc}") from None
        return state.models.describe()

    # ------------------------------------------------------------ 문서
    def _doc_or_404(document_id: str):
        row = state.store.get_document(document_id)
        if row is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        return row

    @app.get("/api/documents")
    def list_documents() -> dict[str, Any]:
        return {"documents": [document_meta(r) for r in state.store.list_documents()],
                "model": state.models.describe()}

    @app.post("/api/documents/upload", status_code=202)
    async def upload(request: Request, filename: str | None = None) -> dict[str, Any]:
        ctype = request.headers.get("content-type", "").lower()
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES + 1024 * 1024:
            raise HTTPException(413, "PDF는 30MB 이하만 올릴 수 있습니다.")
        if ctype.startswith("multipart/form-data"):
            form = await request.form(max_files=1, max_fields=5)
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise HTTPException(400, "multipart 'file' 필드가 필요합니다.")
            name = _safe_filename(file.filename or filename)
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            await file.close()
        elif ctype.startswith("application/pdf"):
            name = _safe_filename(filename)
            buf = bytearray()
            async for part in request.stream():
                buf.extend(part)
                if len(buf) > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "PDF는 30MB 이하만 올릴 수 있습니다.")
            data = bytes(buf)
        else:
            raise HTTPException(415, "multipart/form-data 또는 application/pdf로 올려 주세요.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "PDF는 30MB 이하만 올릴 수 있습니다.")
        if not name.lower().endswith(".pdf"):
            raise HTTPException(400, "PDF 파일(.pdf)만 올릴 수 있습니다.")
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, "PDF 형식이 아닌 파일입니다.")
        return state.uploads.submit(data, name)

    @app.patch("/api/documents/{document_id}")
    def patch_document(document_id: str, body: DocumentPatch) -> dict[str, Any]:
        title = None
        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise HTTPException(400, "제목은 비워 둘 수 없습니다.")
        row = state.store.update_document(document_id, title, body.active)
        if row is None:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        return document_meta(row)

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str) -> dict[str, Any]:
        row = _doc_or_404(document_id)
        return {
            "document_id": row["document_id"], "document_version": row["document_version"], "title": row["title"],
            "page_count": row["page_count"], "chunk_count": row["chunk_count"], "block_count": row["block_count"],
            "active": bool(row["active"]), "pdf_available": Path(row["pdf_path"]).is_file(),
        }

    @app.get("/api/documents/{document_id}/pages/{page}")
    def get_page(
        document_id: str,
        page: int,
        chunk_id: str | None = None,
        highlight: int = Query(default=1, ge=0, le=1),
        scale: float = Query(default=1.5, ge=0.5, le=4.0),
    ) -> Response:
        row = _doc_or_404(document_id)
        if not 1 <= page <= row["page_count"]:
            raise HTTPException(404, "페이지를 찾을 수 없습니다.")
        if not Path(row["pdf_path"]).is_file():
            raise HTTPException(404, "원본 PDF가 없습니다.")
        bboxes: list[list[float]] = []
        if chunk_id:
            chunk = state.store.get_chunk(chunk_id)
            if chunk is None or chunk["document_id"] != document_id:
                raise HTTPException(404, "청크를 찾을 수 없습니다.")
            if highlight:
                spans = json.loads(chunk["source_spans"])
                blocks = state.store.get_blocks([s["block_id"] for s in spans])
                bboxes = [json.loads(b["bbox"]) for b in blocks.values() if b["page"] == page]
        png = render_page_png(row["pdf_path"], page, scale, bboxes)
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})

    @app.get("/api/documents/{document_id}/pages/{page}/text")
    def get_page_text(document_id: str, page: int) -> dict[str, Any]:
        row = _doc_or_404(document_id)
        if not 1 <= page <= row["page_count"]:
            raise HTTPException(404, "페이지를 찾을 수 없습니다.")
        blocks = state.store.get_blocks_for_page(document_id, page)
        return {"document_id": document_id, "page": page, "text": "\n\n".join(b["raw_content"] for b in blocks)}

    @app.get("/api/documents/{document_id}/chunks/{chunk_id}")
    def get_chunk(document_id: str, chunk_id: str) -> dict[str, Any]:
        _doc_or_404(document_id)
        ev = lookup_chunk(state.store, chunk_id)
        if ev is None or ev["document_id"] != document_id:
            raise HTTPException(404, "청크를 찾을 수 없습니다.")
        return {k: ev[k] for k in ("chunk_id", "document_id", "document_version", "pages", "context_text",
                                   "source_refs", "expanded_source_refs")}

    # ------------------------------------------------------------ 잡·질의
    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        row = state.store.get_job(job_id)
        if row is None:
            raise HTTPException(404, "작업을 찾을 수 없습니다.")
        return {
            "job_id": row["job_id"], "kind": row["kind"], "status": row["status"], "stage": row["stage"],
            "progress": row["progress"], "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
        }

    @app.post("/api/queries", status_code=202)
    def create_query(body: QueryRequest) -> dict[str, Any]:
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "질문을 입력해 주세요.")
        doc_ids = list(dict.fromkeys(body.document_ids))
        docs = state.store.get_documents(doc_ids)
        missing = [d for d in doc_ids if d not in docs]
        if missing:
            raise HTTPException(400, "등록되지 않은 문서가 포함되어 있습니다.")
        if not all(docs[d]["active"] for d in doc_ids):
            raise HTTPException(400, "비활성 문서가 포함되어 있습니다.")
        payload = {"question": question, "document_ids": doc_ids, "mode": body.mode, "top_k": body.top_k,
                   "request_ref": body.request_ref or f"req_{uuid.uuid4().hex[:16]}"}
        with state.store.tx() as conn:
            job_id = state.store.create_job(conn, "query", payload)
        state.queue.notify("query")
        return {"job_id": job_id}

    # ------------------------------------------------------------ 정적 SPA
    dist = settings.frontend_dist.resolve()
    if (dist / "index.html").is_file():

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(404, "Not Found")
            target = (dist / full_path).resolve()
            if full_path and target.is_file() and target.is_relative_to(dist):
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app
