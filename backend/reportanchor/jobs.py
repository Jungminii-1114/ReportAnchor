"""SQLite WAL 잡 큐 + 백그라운드 워커.

- 잡은 SQLite에 먼저 기록되고 워커 스레드가 원자적으로 가져간다(재시작 시 실행 중이던 잡은 다시 대기열로).
- PDF 파싱은 데몬 본체와 격리된 자식 프로세스(ProcessPoolExecutor)에서 실행한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .parsing import ParseError, parse_pdf
from .store import Store

log = logging.getLogger("reportanchor.jobs")

Handler = Callable[[str, dict[str, Any], Callable[[str, int], None]], Any]


class JobFailed(Exception):
    """사용자에게 보여도 안전한 메시지를 담은 실패."""


class JobQueue:
    def __init__(self, store: Store):
        self.store = store
        self._handlers: dict[str, tuple[Handler, int]] = {}
        self._events: dict[str, threading.Event] = {}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def register(self, kind: str, handler: Handler, workers: int = 1) -> None:
        self._handlers[kind] = (handler, workers)
        self._events[kind] = threading.Event()

    def start(self) -> None:
        requeued = self.store.requeue_interrupted_jobs()
        if requeued:
            log.info("재시작으로 중단된 잡 %d건을 다시 대기열에 넣었습니다.", requeued)
        for kind, (_, n) in self._handlers.items():
            for i in range(n):
                t = threading.Thread(target=self._loop, args=(kind,), name=f"job-{kind}-{i}", daemon=True)
                t.start()
                self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for ev in self._events.values():
            ev.set()
        for t in self._threads:
            t.join(timeout=5)

    def notify(self, kind: str) -> None:
        self._events[kind].set()

    def _loop(self, kind: str) -> None:
        handler, _ = self._handlers[kind]
        ev = self._events[kind]
        while not self._stop.is_set():
            try:
                row = self.store.claim_job(kind)
            except Exception:  # noqa: BLE001
                log.exception("잡 대기열 조회 실패")
                row = None
            if row is None:
                ev.wait(1.0)
                ev.clear()
                continue
            job_id = row["job_id"]
            payload = json.loads(row["payload"])

            def progress(stage: str, pct: int, _job_id: str = job_id) -> None:
                self.store.update_job(_job_id, stage=stage, progress=pct)

            try:
                result = handler(job_id, payload, progress)
                self.store.update_job(job_id, status="completed", stage="completed", progress=100, result=result)
            except JobFailed as exc:
                self.store.update_job(job_id, status="error", stage="error", error=str(exc))
            except Exception:  # noqa: BLE001 - 내부 상세는 로그에만 남긴다(CWE-209)
                log.exception("잡 실행 실패: %s", job_id)
                self.store.update_job(job_id, status="error", stage="error", error="작업을 처리하지 못했습니다.")


def document_meta(row) -> dict[str, Any]:
    return {
        "document_id": row["document_id"],
        "document_version": row["document_version"],
        "title": row["title"],
        "page_count": row["page_count"],
        "chunk_count": row["chunk_count"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


class UploadService:
    def __init__(self, settings: Settings, store: Store, queue: JobQueue):
        self.settings = settings
        self.store = store
        self.queue = queue
        self._pool: ProcessPoolExecutor | None = None
        self._pool_lock = threading.Lock()

    def _get_pool(self) -> ProcessPoolExecutor:
        with self._pool_lock:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(max_workers=1)
            return self._pool

    def shutdown(self) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None

    def submit(self, data: bytes, filename: str) -> dict[str, Any]:
        sha = hashlib.sha256(data).hexdigest()
        title = Path(filename).stem.strip() or "제목 없음"
        pdf_path = self.settings.pdf_dir / f"{sha}.pdf"
        if not pdf_path.exists():
            tmp = pdf_path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, pdf_path)
        with self.store.tx() as conn:
            existing = conn.execute("SELECT * FROM documents WHERE sha256=?", (sha,)).fetchone()
            if existing is not None:
                # 같은 PDF 재업로드: 새로 만들지 않고 기존 문서를 활성화한다.
                conn.execute("UPDATE documents SET active=1 WHERE document_id=?", (existing["document_id"],))
                meta = document_meta(existing) | {"active": True}
                result = {**meta, "duplicate": True}
                job_id = self.store.create_job(conn, "upload", {"sha256": sha, "filename": filename},
                                               status="completed", stage="completed", progress=100, result=result)
                return {"job_id": job_id, "status": "completed", "document_id": meta["document_id"],
                        "duplicate": True}
            pending = conn.execute(
                "SELECT job_id, status FROM jobs WHERE kind='upload' AND status IN ('queued','running') "
                "AND json_extract(payload, '$.sha256')=?",
                (sha,),
            ).fetchone()
            if pending is not None:
                return {"job_id": pending["job_id"], "status": pending["status"]}
            job_id = self.store.create_job(conn, "upload", {"sha256": sha, "filename": filename, "title": title,
                                                            "pdf_path": str(pdf_path)})
        self.queue.notify("upload")
        return {"job_id": job_id, "status": "queued"}

    def handle(self, job_id: str, payload: dict[str, Any], progress: Callable[[str, int], None]) -> dict[str, Any]:
        progress("parsing", 10)
        future = self._get_pool().submit(parse_pdf, payload["pdf_path"])
        try:
            parsed = future.result(timeout=self.settings.parse_timeout)
        except ParseError as exc:
            raise JobFailed(str(exc)) from None
        except FutureTimeout:
            self.shutdown()  # 멈춘 자식 프로세스를 버리고 다음 잡에서 새로 만든다
            raise JobFailed("PDF 처리 시간이 초과되었습니다.") from None
        progress("indexing", 70)
        with self.store.tx() as conn:
            existing = conn.execute("SELECT * FROM documents WHERE sha256=?", (payload["sha256"],)).fetchone()
            if existing is not None:
                conn.execute("UPDATE documents SET active=1 WHERE document_id=?", (existing["document_id"],))
                return {**document_meta(existing), "active": True, "duplicate": True}
            doc_id = self.store.insert_parsed_document(
                conn, sha256=payload["sha256"], title=payload["title"], filename=payload["filename"],
                pdf_path=payload["pdf_path"], parsed=parsed,
            )
            row = conn.execute("SELECT * FROM documents WHERE document_id=?", (doc_id,)).fetchone()
        return {**document_meta(row), "duplicate": False}
