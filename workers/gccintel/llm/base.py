"""Provider-neutral LLM interface used by the agents.

Agents only ever ask for one of two things: a JSON object that validates against a schema,
or plain text. Every call is metered (tokens, cost, latency) into llm_usage, subject to a
per-run call cap and a per-tenant daily budget, and may be refused before it is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    """No provider configured (or SDK missing). Callers fall back to deterministic logic."""


class LLMBudgetExceeded(LLMError):
    pass


class LLMRefusal(LLMError):
    pass


class LLMInvalidOutput(LLMError):
    pass


@dataclass
class LLMResult:
    text: str
    data: Any | None
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    request_id: str | None = None
    stop_reason: str | None = None
    raw_meta: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str

    def complete_json(self, *, model: str, system: str, user: str, schema: dict, schema_name: str,
                      max_tokens: int, effort: str) -> LLMResult: ...

    def complete_text(self, *, model: str, system: str, messages: list[dict], max_tokens: int,
                      effort: str) -> LLMResult: ...
