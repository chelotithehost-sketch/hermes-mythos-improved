"""
Hermes-Mythos Configuration — validated settings with startup warnings.

Loads from environment variables and .env file, validates provider
availability, and exposes a frozen config object.
"""

from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider tier definitions
# ---------------------------------------------------------------------------

FRONTIER_PROVIDERS = ("openai", "anthropic", "gemini")
MID_TIER_PROVIDERS = ("mistral",)
LIGHTWEIGHT_PROVIDERS = ("ollama",)

ALL_PROVIDERS = FRONTIER_PROVIDERS + MID_TIER_PROVIDERS + LIGHTWEIGHT_PROVIDERS

# Default fallback chain: frontier → mid-tier → lightweight
DEFAULT_FALLBACK_CHAIN: List[str] = [
    "openai", "anthropic", "gemini", "mistral", "ollama",
]


def _env(key: str, default: str = "") -> str:
    """Read an environment variable with a default."""
    return os.getenv(key, default).strip()


# ---------------------------------------------------------------------------
# Dataclass config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    # --- LLM providers ---
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    mistral_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # Fallback chain order
    fallback_chain: tuple = field(default_factory=lambda: tuple(DEFAULT_FALLBACK_CHAIN))

    # Default model per provider
    default_models: dict = field(default_factory=lambda: {
        "openai": "gpt-4o",
        "anthropic": "claude-sonnet-4-20250514",
        "gemini": "gemini-2.0-flash",
        "mistral": "mistral-large-latest",
        "ollama": "llama3",
    })

    # --- Pipeline ---
    max_revisions: int = 3
    chapter_count: int = 12
    words_per_chapter: int = 3000

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: str = "data"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- WhatsApp (Twilio) ---
    whatsapp_account_sid: str = ""
    whatsapp_auth_token: str = ""
    whatsapp_from: str = ""
    whatsapp_to: str = ""

    # --- Database ---
    db_path: str = "data/hermes.db"

    # -----------------------------------------------------------------------
    # Computed helpers
    # -----------------------------------------------------------------------

    @property
    def available_providers(self) -> List[str]:
        """Return provider names that have credentials configured."""
        available = []
        if self.openai_api_key:
            available.append("openai")
        if self.anthropic_api_key:
            available.append("anthropic")
        if self.gemini_api_key:
            available.append("gemini")
        if self.mistral_api_key:
            available.append("mistral")
        # Ollama is always available (local)
        available.append("ollama")
        return available

    @property
    def active_fallback_chain(self) -> List[str]:
        """Fallback chain filtered to available providers."""
        avail = set(self.available_providers)
        return [p for p in self.fallback_chain if p in avail]

    def model_for(self, provider: str) -> str:
        """Return the default model name for a provider."""
        return self.default_models.get(provider, "gpt-4o")


# ---------------------------------------------------------------------------
# Factory with validation
# ---------------------------------------------------------------------------

def load_config() -> Config:
    """Build and validate configuration from environment variables.

    Warns at startup if frontier providers are unavailable so operators
    know the pipeline will rely on lightweight models.
    """
    # Try loading .env if python-dotenv is installed
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    cfg = Config(
        openai_api_key=_env("OPENAI_API_KEY"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        gemini_api_key=_env("GEMINI_API_KEY"),
        mistral_api_key=_env("MISTRAL_API_KEY"),
        ollama_base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        whatsapp_account_sid=_env("WHATSAPP_ACCOUNT_SID"),
        whatsapp_auth_token=_env("WHATSAPP_AUTH_TOKEN"),
        whatsapp_from=_env("WHATSAPP_FROM"),
        whatsapp_to=_env("WHATSAPP_TO"),
        host=_env("HOST", "0.0.0.0"),
        port=int(_env("PORT", "8000")),
        data_dir=_env("DATA_DIR", "data"),
        db_path=_env("DB_PATH", "data/hermes.db"),
        max_revisions=int(_env("MAX_REVISIONS", "3")),
        chapter_count=int(_env("CHAPTER_COUNT", "12")),
        words_per_chapter=int(_env("WORDS_PER_CHAPTER", "3000")),
    )

    # Ensure data directory exists
    Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)

    # --- Validation warnings ---
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """Emit warnings for missing or misconfigured settings."""
    avail = cfg.available_providers
    chain = cfg.active_fallback_chain

    if not chain:
        logger.warning(
            "No LLM providers are configured. Only Ollama (local) will be "
            "available. Set API keys in environment or .env file."
        )

    frontier_available = [p for p in FRONTIER_PROVIDERS if p in avail]
    if not frontier_available:
        logger.warning(
            "No frontier LLM providers (OpenAI, Anthropic, Gemini) are "
            "available. Pipeline quality may be reduced. Available: %s",
            avail,
        )

    if cfg.max_revisions < 0:
        raise ValueError(f"max_revisions must be >= 0, got {cfg.max_revisions}")

    if cfg.chapter_count < 1:
        raise ValueError(f"chapter_count must be >= 1, got {cfg.chapter_count}")

    if cfg.words_per_chapter < 100:
        raise ValueError(
            f"words_per_chapter must be >= 100, got {cfg.words_per_chapter}"
        )

    # Warn if DB path parent doesn't exist
    db_parent = Path(cfg.db_path).parent
    if not db_parent.exists():
        logger.warning("DB directory %s does not exist, will be created.", db_parent)

    logger.info("Config loaded — available providers: %s", avail)
    logger.info("Fallback chain: %s", chain)
