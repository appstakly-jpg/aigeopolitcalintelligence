"""Claude via the official Anthropic Python SDK.

Defaults follow current Claude API guidance: model claude-opus-5-5, effort set explicitly
(the model's default is medium), structured outputs via output_config.format, server-side
refusal fallbacks enabled (`fallbacks="default"`, beta server-side-fallback-2026-07-01),
and stop_reason checked before content is read.
"""

from __future__ import annotations

import json
import os
import time

from .base import LLMInvalidOutput, LLMRefusal, LLMResult, LLMUnavailable

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, timeout_seconds: float = 120.0, use_fallbacks: bool = True):
        try:
            import anthropic  # noqa: PLC0415 - optional dependency
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("anthropic SDK not installed (pip install 'gccintel[llm]')") from exc
        self._anthropic = anthropic
        # Explicit base URL so an ambient ANTHROPIC_BASE_URL (e.g. a platform proxy) is never picked up silently.
        self.client = anthropic.Anthropic(timeout=timeout_seconds, max_retries=2,
                                          base_url=os.environ.get("LLM_ANTHROPIC_BASE_URL") or "https://api.anthropic.com")
        self.use_fallbacks = use_fallbacks

    def _create(self, **kwargs):
        if self.use_fallbacks:
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        return self.client.messages.create(**kwargs)

    def _result(self, response, started: float, data=None) -> LLMResult:
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        usage = response.usage
        return LLMResult(
            text=text, data=data, provider=self.name, model=response.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_id=getattr(response, "_request_id", None), stop_reason=response.stop_reason,
        )

    def _check_stop(self, response) -> None:
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMRefusal(f"model declined (category={getattr(details, 'category', None)})")
        if response.stop_reason == "max_tokens":
            raise LLMInvalidOutput("output truncated at max_tokens")

    def complete_json(self, *, model, system, user, schema, schema_name, max_tokens, effort) -> LLMResult:
        started = time.monotonic()
        response = self._create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        self._check_stop(response)
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMInvalidOutput(f"invalid JSON from model: {exc}") from exc
        return self._result(response, started, data)

    def complete_text(self, *, model, system, messages, max_tokens, effort) -> LLMResult:
        started = time.monotonic()
        response = self._create(model=model, max_tokens=max_tokens, system=system, messages=messages,
                                output_config={"effort": effort})
        self._check_stop(response)
        return self._result(response, started)
