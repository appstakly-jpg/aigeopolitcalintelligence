"""OpenAI via the official SDK (Responses API, strict JSON schema output).

The model must be configured explicitly (OPENAI_MODEL / LLM_MODEL); no model name is
assumed. Untested against the live API in the build environment (no credentials).
"""

from __future__ import annotations

import json
import time

from .base import LLMInvalidOutput, LLMResult, LLMUnavailable

_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


class OpenAIProvider:
    name = "openai"

    def __init__(self, timeout_seconds: float = 120.0, reasoning: bool = False):
        try:
            import openai  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("openai SDK not installed") from exc
        self.client = openai.OpenAI(timeout=timeout_seconds, max_retries=2)
        self.reasoning = reasoning

    def _usage(self, response, started) -> dict:
        usage = getattr(response, "usage", None)
        return {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "request_id": getattr(response, "id", None),
        }

    def complete_json(self, *, model, system, user, schema, schema_name, max_tokens, effort) -> LLMResult:
        started = time.monotonic()
        kwargs = {}
        if self.reasoning:
            kwargs["reasoning"] = {"effort": _EFFORT.get(effort, "medium")}
        response = self.client.responses.create(
            model=model, instructions=system, input=user, max_output_tokens=max_tokens,
            text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
            **kwargs,
        )
        text = response.output_text
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMInvalidOutput(f"invalid JSON from model: {exc}") from exc
        return LLMResult(text=text, data=data, provider=self.name, model=model, **self._usage(response, started))

    def complete_text(self, *, model, system, messages, max_tokens, effort) -> LLMResult:
        started = time.monotonic()
        response = self.client.responses.create(model=model, instructions=system, input=messages,
                                                max_output_tokens=max_tokens)
        return LLMResult(text=response.output_text, data=None, provider=self.name, model=model,
                         **self._usage(response, started))
