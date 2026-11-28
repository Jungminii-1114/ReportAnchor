"""개인 PC 정본 저장소: 단일 SQLite(WAL) + 파일시스템.

연결은 작업마다 새로 열어 스레드 간 공유하지 않는다. 쓰기는 `BEGIN IMMEDIATE` 트랜잭션으로 원자적으로 처리한다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id      TEXT PRIMARY KEY,
    sha256           TEXT NOT NULL UNIQUE,
    document_version TEXT NOT NULL,
    title            TEXT NOT NULL,
    filename         TEXT,
    page_count       INTEGER NOT NULL,
    chunk_count      INTEGER NOT NULL,
    block_count      INTEGER NOT NULL,
    active           INTEGER NOT NULL DEFAULT 1,
    pdf_path         TEXT NOT NULL,
    tokenizer        TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
    block_id     TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    page         INTEGER NOT NULL,
    bbox         TEXT NOT NULL,
    raw_content  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blocks_doc_seq ON blocks(document_id, seq);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id         TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    document_version TEXT NOT NULL,
    seq              INTEGER NOT NULL,
    text             TEXT NOT NULL,
    pages            TEXT NOT NULL,
    source_spans     TEXT NOT NULL,
    tokens           TEXT NOT NULL,
    tokenizer        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_seq ON chunks(document_id, seq);
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    progress    INTEGER,
    payload     TEXT NOT NULL,
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(kind, status, created_at);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    # ---- 연결 ----
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def init(self) -> None:
        with self.read() as conn:
            conn.executescript(SCHEMA)

    # ---- 설정 ----
    def get_setting(self, key: str) -> Any | None:
        with self.read() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def put_setting(self, key: str, value: Any) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # ---- 문서 ----
    def list_documents(self) -> list[sqlite3.Row]:
        with self.read() as conn:
            return conn.execute("SELECT * FROM documents ORDER BY created_at DESC, rowid DESC").fetchall()

    def get_document(self, document_id: str) -> sqlite3.Row | None:
        with self.read() as conn:
            return conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()

    def get_documents(self, document_ids: list[str]) -> dict[str, sqlite3.Row]:
        if not document_ids:
            return {}
        marks = ",".join("?" * len(document_ids))
        with self.read() as conn:
            rows = conn.execute(f"SELECT * FROM documents WHERE document_id IN ({marks})", document_ids).fetchall()
        return {r["document_id"]: r for r in rows}

    def update_document(self, document_id: str, title: str | None, active: bool | None) -> sqlite3.Row | None:
        with self.tx() as conn:
            if conn.execute("SELECT 1 FROM documents WHERE document_id=?", (document_id,)).fetchone() is None:
                return None
            if title is not None:
                conn.execute("UPDATE documents SET title=?, updated_at=? WHERE document_id=?", (title, now_iso(), document_id))
            if active is not None:
                conn.execute(
                    "UPDATE documents SET active=?, updated_at=? WHERE document_id=?", (int(active), now_iso(), document_id)
                )
        return self.get_document(document_id)

    def insert_parsed_document(
        self, conn: sqlite3.Connection, *, sha256: str, title: str, filename: str, pdf_path: str, parsed: dict[str, Any]
    ) -> str:
        """파싱 결과를 한 트랜잭션 안에서 문서·블록·청크로 저장한다(호출자가 tx를 연다)."""
        document_id = new_id("doc")
        version = f"sha256-{sha256[:16]}"
        key = sha256[:16]
        ts = now_iso()
        blocks = parsed["blocks"]
        chunks = parsed["chunks"]
        block_ids = [f"blk_{key}_{b['seq']:05d}" for b in blocks]
        conn.execute(
            """INSERT INTO documents(document_id, sha256, document_version, title, filename, page_count, chunk_count,
               block_count, active, pdf_path, tokenizer, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?)""",
            (document_id, sha256, version, title, filename, parsed["page_count"], len(chunks), len(blocks),
             pdf_path, parsed["tokenizer"], ts, ts),
        )
        conn.executemany(
            "INSERT INTO blocks(block_id, document_id, seq, page, bbox, raw_content) VALUES(?,?,?,?,?,?)",
            [(block_ids[b["seq"]], document_id, b["seq"], b["page"], json.dumps(b["bbox"]), b["raw_content"]) for b in blocks],
        )
        conn.executemany(
            """INSERT INTO chunks(chunk_id, document_id, document_version, seq, text, pages, source_spans, tokens, tokenizer)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                (
                    f"chk_{key}_{c['seq']:05d}",
                    document_id,
                    version,
                    c["seq"],
                    c["text"],
                    json.dumps(c["pages"]),
                    json.dumps(
                        [{"block_id": block_ids[s["block_seq"]], "start": s["start"], "end": s["end"]} for s in c["spans"]]
                    ),
                    json.dumps(c["tokens"], ensure_ascii=False),
                    parsed["tokenizer"],
                )
                for c in chunks
            ],
        )
        return document_id

    # ---- 청크·블록 ----
    def load_chunks_for_search(self, document_ids: list[str]) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(document_ids))
        with self.read() as conn:
            return conn.execute(
                f"""SELECT chunk_id, document_id, document_version, seq, text, tokens, tokenizer FROM chunks
                    WHERE document_id IN ({marks}) ORDER BY document_id, seq""",
                document_ids,
            ).fetchall()

    def get_chunk(self, chunk_id: str) -> sqlite3.Row | None:
        with self.read() as conn:
            return conn.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()

    def get_blocks_by_seq_range(self, document_id: str, lo: int, hi: int) -> list[sqlite3.Row]:
        with self.read() as conn:
            return conn.execute(
                "SELECT * FROM blocks WHERE document_id=? AND seq BETWEEN ? AND ? ORDER BY seq", (document_id, lo, hi)
            ).fetchall()

    def get_blocks_for_page(self, document_id: str, page: int) -> list[sqlite3.Row]:
        with self.read() as conn:
            return conn.execute(
                "SELECT * FROM blocks WHERE document_id=? AND page=? ORDER BY seq", (document_id, page)
            ).fetchall()

    def get_blocks(self, block_ids: list[str]) -> dict[str, sqlite3.Row]:
        if not block_ids:
            return {}
        marks = ",".join("?" * len(block_ids))
        with self.read() as conn:
            rows = conn.execute(f"SELECT * FROM blocks WHERE block_id IN ({marks})", block_ids).fetchall()
        return {r["block_id"]: r for r in rows}

    # ---- 잡 큐 ----
    def create_job(self, conn: sqlite3.Connection, kind: str, payload: dict[str, Any], *, status: str = "queued",
                   stage: str = "queued", progress: int | None = 0, result: Any = None) -> str:
        job_id = new_id("job")
        ts = now_iso()
        conn.execute(
            """INSERT INTO jobs(job_id, kind, status, stage, progress, payload, result, error, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,NULL,?,?)""",
            (job_id, kind, status, stage, progress, json.dumps(payload, ensure_ascii=False),
             None if result is None else json.dumps(result, ensure_ascii=False), ts, ts),
        )
        return job_id

    def claim_job(self, kind: str) -> sqlite3.Row | None:
        with self.tx() as conn:
            return conn.execute(
                """UPDATE jobs SET status='running', stage='starting', updated_at=?
                   WHERE job_id=(SELECT job_id FROM jobs WHERE kind=? AND status='queued' ORDER BY created_at, rowid LIMIT 1)
                     AND status='queued'
                   RETURNING job_id, payload""",
                (now_iso(), kind),
            ).fetchone()

    def update_job(self, job_id: str, *, status: str | None = None, stage: str | None = None,
                   progress: int | None = None, result: Any = None, error: str | None = None) -> None:
        sets: list[str] = ["updated_at=?"]
        args: list[Any] = [now_iso()]
        if status is not None:
            sets.append("status=?"); args.append(status)
        if stage is not None:
            sets.append("stage=?"); args.append(stage)
        if progress is not None:
            sets.append("progress=?"); args.append(progress)
        if result is not None:
            sets.append("result=?"); args.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            sets.append("error=?"); args.append(error)
        args.append(job_id)
        with self.tx() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id=?", args)

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        with self.read() as conn:
            return conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()

    def requeue_interrupted_jobs(self) -> int:
        with self.tx() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='queued', stage='queued', progress=0, updated_at=? WHERE status='running'",
                (now_iso(),),
            )
            return cur.rowcount
