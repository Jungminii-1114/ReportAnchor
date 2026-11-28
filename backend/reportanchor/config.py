"""실행 설정. 환경 변수에서 읽고, 테스트에서는 직접 생성해 주입한다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

LOCAL_BASE_URL = "http://127.0.0.1:11434"
CLOUD_BASE_URL = "https://ollama.com"
CLOUD_DEFAULT_MODEL = "deepseek-v4.1-flash"
DEFAULT_ALLOWED_OLLAMA_HOSTS = (
    "http://127.0.0.1:11434",
    "http://localhost:11434",
    "https://ollama.com",
)
DEV_UI_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: BACKEND_DIR / ".data")
    frontend_dist: Path = field(default_factory=lambda: REPO_DIR / "frontend" / "dist")
    port: int = 8765
    extra_allowed_ollama_hosts: tuple[str, ...] = ()
    api_key: str | None = None
    autodetect_model: bool = True
    local_model: str | None = None
    llm_timeout: float = 120.0
    parse_timeout: float = 600.0
    query_workers: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        extra = tuple(
            h.strip() for h in os.environ.get("REPORTANCHOR_ALLOWED_OLLAMA_HOSTS", "").split(",") if h.strip()
        )
        data_dir = os.environ.get("REPORTANCHOR_DATA_DIR")
        return cls(
            data_dir=Path(data_dir) if data_dir else BACKEND_DIR / ".data",
            port=int(os.environ.get("REPORTANCHOR_PORT", "8765")),
            extra_allowed_ollama_hosts=extra,
            api_key=(os.environ.get("OLLAMA_API_KEY") or "").strip() or None,
            local_model=(os.environ.get("REPORTANCHOR_OLLAMA_MODEL") or "").strip() or None,
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "reportanchor.db"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def artifact_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.pdf_dir, self.artifact_dir):
            d.mkdir(parents=True, exist_ok=True)
