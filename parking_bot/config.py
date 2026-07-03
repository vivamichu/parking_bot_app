"""Central configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of this package directory.
ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _abs(path: str) -> str:
    """Resolve a possibly-relative path against the project root."""
    p = Path(path)
    return str(p if p.is_absolute() else (ROOT / p))


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    chat_model: str = os.getenv("CHAT_MODEL", "gpt-4o-mini")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    # NOTE: not named MILVUS_URI — pymilvus reads that env var at import time.
    milvus_uri: str = _abs(os.getenv("MILVUS_DB_PATH", "./data/milvus_parking.db"))
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "parking_static")
    sqlite_path: str = _abs(os.getenv("SQLITE_PATH", "./data/parking_dynamic.db"))

    static_dir: str = str(ROOT / "data" / "static")

    # Retrieval defaults
    top_k: int = int(os.getenv("TOP_K", "4"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "600"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "100"))

    # --- Stage 3: MCP reservation-store server ---
    # Where confirmed reservations are written (one pipe-delimited line each).
    confirmed_reservations_path: str = _abs(
        os.getenv("CONFIRMED_RESERVATIONS_PATH", "./data/confirmed_reservations.txt")
    )
    # Route confirmed writes through the MCP server when true; otherwise the
    # admin agent writes to the store directly (same file & format either way).
    mcp_enabled: bool = os.getenv("MCP_ENABLED", "false").lower() in {"1", "true", "yes"}
    mcp_host: str = os.getenv("MCP_HOST", "127.0.0.1")
    mcp_port: int = int(os.getenv("MCP_PORT", "8765"))
    mcp_path: str = os.getenv("MCP_PATH", "/mcp")
    # Bearer token protecting the MCP server. Empty => the server is fail-closed
    # (all requests rejected), so a token MUST be set to accept writes.
    mcp_auth_token: str = os.getenv("MCP_AUTH_TOKEN", "")

    @property
    def mcp_url(self) -> str:
        return f"http://{self.mcp_host}:{self.mcp_port}{self.mcp_path}"

    def require_openai(self) -> None:
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
            )


settings = Settings()
