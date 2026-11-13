"""LLM service: provider selection, budgets, call caps and usage accounting."""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from ..config import Settings, get_settings
from ..db import execute, fetch_one
from ..log import get_logger
from .base import LLMBudgetExceeded, LLMError, LLMProvider, LLMResult, LLMUnavailable
from .pricing import cost_usd

log = get_logger(__name__)


def build_provider(settings: Settings) -> LLMProvider | None:
    name = settings.llm_provider
    if name in ("", "none"):
        return None
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider  # noqa: PLC0415
        return AnthropicProvider(timeout_seconds=settings.llm_timeout_seconds)
    if name == "openai":
        from .openai_provider import OpenAIProvider  # noqa: PLC0415
        return OpenAIProvider(timeout_seconds=settings.llm_timeout_seconds)
    if name == "gemini":
        from .gemini_provider import GeminiProvider  # noqa: PLC0415
        return GeminiProvider(timeout_seconds=settings.llm_timeout_seconds)
    if name == "mock":
        if settings.environment == "production":
            raise LLMUnavailable("mock LLM provider is not allowed in production")
        from .mock_provider import MockProvider  # noqa: PLC0415
        return MockProvider()
    raise LLMUnavailable(f"unknown LLM_PROVIDER {name!r}")


class LLMService:
    """One instance per pipeline run and tenant."""

    def __init__(self, conn: psycopg.Connection, tenant_id: str | None, provider: LLMProvider | None,
                 settings: Settings | None = None, max_calls: int | None = None):
        self.conn = conn
        self.tenant_id = tenant_id
        self.provider = provider
        self.settings = settings or get_settings()
        self.max_calls = max_calls if max_calls is not None else self.settings.llm_max_calls_per_run
        self.calls = 0
        self.cost = 0.0

    @property
    def available(self) -> bool:
        return self.provider is not None

    def model_for(self, task: str) -> str:
        s = self.settings
        model = {"extraction": s.llm_model_extraction, "translation": s.llm_model_translation,
                 "briefing": s.llm_model_briefing}.get(task) or s.llm_model_default
        if not model:
            raise LLMUnavailable("no model configured for this provider (set LLM_MODEL)")
        return model

    def _check_budget(self) -> None:
        if self.calls >= self.max_calls:
            raise LLMBudgetExceeded(f"per-run LLM call cap reached ({self.max_calls})")
        row = fetch_one(self.conn,
                        """select coalesce(sum(cost_usd), 0) as spent from llm_usage
                           where (tenant_id = %s or (%s::uuid is null and tenant_id is null))
                             and created_at >= date_trunc('day', now())""",
                        (self.tenant_id, self.tenant_id))
        spent = float(row["spent"]) if row else 0.0
        if spent >= self.settings.llm_daily_budget_usd:
            raise LLMBudgetExceeded(f"daily LLM budget reached (${spent:.2f} >= ${self.settings.llm_daily_budget_usd:.2f})")

    def _record(self, agent: str, model: str, result: LLMResult | None, error: str | None) -> None:
        cost, known = (0.0, False)
        if result:
            cost, known = cost_usd(result.model or model, result.input_tokens, result.output_tokens,
                                   result.cache_read_tokens, result.cache_write_tokens)
            self.cost += cost
        execute(self.conn, """
            insert into llm_usage(tenant_id, agent, provider, model, input_tokens, output_tokens, cache_read_tokens,
                                  cache_write_tokens, cost_usd, latency_ms, success, error, request_id)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (self.tenant_id, agent, self.provider.name if self.provider else "none", result.model if result else model,
                 result.input_tokens if result else 0, result.output_tokens if result else 0,
                 result.cache_read_tokens if result else 0, result.cache_write_tokens if result else 0,
                 cost, result.latency_ms if result else None, error is None,
                 (error or ("price_unknown" if result and not known else None)), result.request_id if result else None))

    def json(self, *, agent: str, task: str, system: str, user: str, schema: dict, schema_name: str,
             max_tokens: int = 4000, effort: str = "low") -> LLMResult:
        if not self.provider:
            raise LLMUnavailable("no LLM provider configured")
        self._check_budget()
        model = self.model_for(task)
        self.calls += 1
        try:
            result = self.provider.complete_json(model=model, system=system, user=user, schema=schema,
                                                 schema_name=schema_name, max_tokens=max_tokens, effort=effort)
        except LLMError as exc:
            self._record(agent, model, None, f"{type(exc).__name__}: {exc}")
            raise
        except Exception as exc:  # provider SDK errors
            self._record(agent, model, None, f"{type(exc).__name__}: {exc}"[:500])
            raise LLMError(str(exc)) from exc
        self._record(agent, model, result, None)
        return result

    def text(self, *, agent: str, task: str, system: str, messages: list[dict], max_tokens: int = 4000,
             effort: str = "medium") -> LLMResult:
        if not self.provider:
            raise LLMUnavailable("no LLM provider configured")
        self._check_budget()
        model = self.model_for(task)
        self.calls += 1
        try:
            result = self.provider.complete_text(model=model, system=system, messages=messages,
                                                 max_tokens=max_tokens, effort=effort)
        except LLMError as exc:
            self._record(agent, model, None, f"{type(exc).__name__}: {exc}")
            raise
        except Exception as exc:
            self._record(agent, model, None, f"{type(exc).__name__}: {exc}"[:500])
            raise LLMError(str(exc)) from exc
        self._record(agent, model, result, None)
        return result


def now_utc() -> datetime:
    return datetime.now(UTC)
