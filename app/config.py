"""Central configuration. Every setting comes from environment variables (.env in development).

Nothing secret is ever sent to the browser: only the values in `public_config()` are exposed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# The API key in this project's .env always wins over one saved in Windows/system environment
# variables; otherwise an old key set elsewhere would silently be used instead.
_dotenv_key = (dotenv_values(BASE_DIR / ".env").get("OPENAI_API_KEY") or "").strip()
if _dotenv_key and not _dotenv_key.startswith("sk-your"):
    os.environ["OPENAI_API_KEY"] = _dotenv_key


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DEFAULT_SOURCE_URLS = (
    "https://kabila96.github.io/powellndlovuportfolio/,"
    "https://kabila96.github.io/online-cv-resume/,"
    "https://kabila96.github.io/GeoAI-Portfolio/"
)


@dataclass(frozen=True)
class Settings:
    # --- OpenAI ---------------------------------------------------------
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    chat_model: str = field(default_factory=lambda: os.getenv("CHAT_MODEL", "gpt-4o-mini"))
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    )
    temperature: float = field(default_factory=lambda: _float("TEMPERATURE", 0.2))
    max_output_tokens: int = field(default_factory=lambda: _int("MAX_OUTPUT_TOKENS", 600))
    openai_timeout_seconds: float = field(default_factory=lambda: _float("OPENAI_TIMEOUT_SECONDS", 30))

    # --- Knowledge base -------------------------------------------------
    docx_path: Path = field(
        default_factory=lambda: BASE_DIR / os.getenv("DOCX_PATH", "data/consultancy_information.docx")
    )
    source_urls: list[str] = field(default_factory=lambda: _list("SOURCE_URLS", DEFAULT_SOURCE_URLS))
    index_dir: Path = field(default_factory=lambda: BASE_DIR / os.getenv("INDEX_DIR", "index"))
    top_k: int = field(default_factory=lambda: _int("TOP_K", 6))
    min_similarity: float = field(default_factory=lambda: _float("MIN_SIMILARITY", 0.25))

    # --- API protection -------------------------------------------------
    max_message_chars: int = field(default_factory=lambda: _int("MAX_MESSAGE_CHARS", 1000))
    max_history_turns: int = field(default_factory=lambda: _int("MAX_HISTORY_TURNS", 6))
    rate_limit_per_minute: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_MINUTE", 10))
    rate_limit_per_day: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_DAY", 200))
    allowed_origins: list[str] = field(default_factory=lambda: _list("ALLOWED_ORIGINS", ""))
    # Sites allowed to show the chatbot inside an <iframe> (e.g. https://kabila96.github.io).
    embed_origins: list[str] = field(default_factory=lambda: _list("EMBED_ORIGINS", ""))
    trust_proxy_headers: bool = field(
        default_factory=lambda: os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
    )

    # --- Public contact details shown in the UI ----------------------------
    contact_email: str = field(default_factory=lambda: os.getenv("CONTACT_EMAIL", "[CONTACT_EMAIL]"))
    phone_number: str = field(default_factory=lambda: os.getenv("PHONE_NUMBER", "[PHONE_NUMBER]"))
    linkedin_url: str = field(default_factory=lambda: os.getenv("LINKEDIN_URL", "[LINKEDIN_URL]"))
    github_url: str = field(default_factory=lambda: os.getenv("GITHUB_URL", ""))


settings = Settings()


def public_config() -> dict:
    """Safe, non-secret values that the frontend may read from /api/config."""
    return {
        "contact": {
            "email": settings.contact_email,
            "phone": settings.phone_number,
            "linkedin": settings.linkedin_url,
            "github": settings.github_url,
        },
        "portfolios": [
            {"label": "Main portfolio", "url": settings.source_urls[0] if settings.source_urls else ""},
            {"label": "Online CV", "url": settings.source_urls[1] if len(settings.source_urls) > 1 else ""},
            {"label": "GeoAI portfolio", "url": settings.source_urls[2] if len(settings.source_urls) > 2 else ""},
        ],
        "max_message_chars": settings.max_message_chars,
        "model": settings.chat_model,
    }
