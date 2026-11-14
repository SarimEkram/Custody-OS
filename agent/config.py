"""Settings loaded from .env. Import `settings` everywhere; never read os.environ directly."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    mongodb_uri_agent: str = _env("MONGODB_URI_AGENT")
    mongodb_uri_scanner: str = _env("MONGODB_URI_SCANNER")
    mongodb_uri_admin: str = _env("MONGODB_URI_ADMIN")
    mongodb_db: str = _env("MONGODB_DB", "custody")

    groq_api_key: str = _env("GROQ_API_KEY")
    llm_base_url: str = _env("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    llm_model: str = _env("LLM_MODEL", "openai/gpt-oss-120b")

    embeddings_provider: str = _env("EMBEDDINGS_PROVIDER", "voyage")  # voyage | atlas_auto
    voyage_api_key: str = _env("VOYAGE_API_KEY")
    voyage_base_url: str = _env("VOYAGE_BASE_URL", "https://ai.mongodb.com/v1")
    voyage_model: str = _env("VOYAGE_MODEL", "voyage-3.5")
    auto_embed_model: str = _env("AUTO_EMBED_MODEL", "voyage-4")  # Atlas Automated Embedding model
    embed_dim: int = int(_env("EMBED_DIM", "1024"))

    scanner_hmac_secret: str = _env("SCANNER_HMAC_SECRET")
    verifier_timeout_seconds: float = float(_env("VERIFIER_TIMEOUT_SECONDS", "10"))


settings = Settings()

AGENT_ID = "agent:custody-01"
