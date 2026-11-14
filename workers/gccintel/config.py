"""Runtime configuration, read exclusively from environment variables.

Secrets (API keys, SMTP passwords, encryption keys) are never read from files or the
database; see `.env.example` for the full list and what each one enables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError as exc:  # fail fast on misconfiguration
        raise RuntimeError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    database_url: str
    environment: str
    demo_mode: bool
    log_level: str
    log_json: bool

    # HTTP / ingestion politeness
    http_user_agent: str
    http_timeout_seconds: float
    http_max_bytes: int
    per_host_min_interval_seconds: float
    respect_robots: bool
    allow_private_network_fetch: bool

    # LLM
    llm_provider: str  # anthropic | openai | gemini | mock | none
    llm_model_default: str | None
    llm_model_extraction: str | None
    llm_model_translation: str | None
    llm_model_briefing: str | None
    llm_max_calls_per_run: int
    llm_daily_budget_usd: float
    llm_timeout_seconds: float

    # Notifications
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_from: str
    smtp_starttls: bool
    telegram_bot_token: str | None
    app_base_url: str

    # Security
    app_encryption_key: str | None

    # Worker
    worker_id: str
    worker_poll_seconds: float
    pdf_export_enabled: bool

    extra: dict[str, str] = field(default_factory=dict)

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider not in ("", "none")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if not provider:
        # Auto-detect from available credentials; never pretend a provider is configured.
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_MODEL"):
            provider = "openai"
        elif os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL"):
            provider = "gemini"
        else:
            provider = "none"
    default_model = os.environ.get("LLM_MODEL") or {
        "anthropic": "claude-opus-5-5",
        "openai": os.environ.get("OPENAI_MODEL"),
        "gemini": os.environ.get("GEMINI_MODEL"),
        "mock": "mock-1",
    }.get(provider)
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/gccintel"),
        environment=os.environ.get("APP_ENV", "development"),
        demo_mode=_bool("DEMO_MODE", False),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        log_json=_bool("LOG_JSON", True),
        http_user_agent=os.environ.get(
            "HTTP_USER_AGENT",
            "GCCIntelBot/0.1 (+public-source monitoring; respects robots.txt)",
        ),
        http_timeout_seconds=_float("HTTP_TIMEOUT_SECONDS", 20.0),
        http_max_bytes=_int("HTTP_MAX_BYTES", 5_000_000),
        per_host_min_interval_seconds=_float("HTTP_PER_HOST_MIN_INTERVAL_SECONDS", 2.0),
        respect_robots=_bool("RESPECT_ROBOTS_TXT", True),
        allow_private_network_fetch=_bool("ALLOW_PRIVATE_NETWORK_FETCH", False),
        llm_provider=provider,
        llm_model_default=default_model,
        llm_model_extraction=os.environ.get("LLM_MODEL_EXTRACTION") or default_model,
        llm_model_translation=os.environ.get("LLM_MODEL_TRANSLATION") or default_model,
        llm_model_briefing=os.environ.get("LLM_MODEL_BRIEFING") or default_model,
        llm_max_calls_per_run=_int("LLM_MAX_CALLS_PER_RUN", 200),
        llm_daily_budget_usd=_float("LLM_DAILY_BUDGET_USD", 10.0),
        llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 120.0),
        smtp_host=os.environ.get("SMTP_HOST") or None,
        smtp_port=_int("SMTP_PORT", 587),
        smtp_username=os.environ.get("SMTP_USERNAME") or None,
        smtp_password=os.environ.get("SMTP_PASSWORD") or None,
        smtp_from=os.environ.get("SMTP_FROM", "alerts@localhost"),
        smtp_starttls=_bool("SMTP_STARTTLS", True),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:3000").rstrip("/"),
        app_encryption_key=os.environ.get("APP_ENCRYPTION_KEY") or None,
        worker_id=os.environ.get("WORKER_ID", f"worker-{os.getpid()}"),
        worker_poll_seconds=_float("WORKER_POLL_SECONDS", 2.0),
        pdf_export_enabled=_bool("PDF_EXPORT_ENABLED", True),
    )


def reset_settings_cache() -> None:
    get_settings.cache_clear()
