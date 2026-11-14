"""Gemini via the official google-genai SDK (JSON schema response).

The model must be configured explicitly (GEMINI_MODEL / LLM_MODEL). Untested against the
live API in the build environment (no credentials).
"""

from __future__ import annotations

import json
import os
import time

from .base import LLMInvalidOutput, LLMResult, LLMUnavailable


class GeminiProvider:
    name = "gemini"

    def __init__(self, timeout_seconds: float = 120.0):
        try:
            from google import genai  # noqa: PLC0415
            from google.genai import types  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("google-genai SDK not installed") from exc
        self.types = types
        self.client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"),
                                   http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)))

    def _usage(self, response, started) -> dict:
        meta = getattr(response, "usage_metadata", None)
        return {
            "input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    def complete_json(self, *, model, system, user, schema, schema_name, max_tokens, effort) -> LLMResult:
        started = time.monotonic()
        response = self.client.models.generate_content(
            model=model, contents=user,
            config=self.types.GenerateContentConfig(
                system_instruction=system, max_output_tokens=max_tokens,
                response_mime_type="application/json", response_json_schema=schema,
            ),
        )
        text = response.text or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMInvalidOutput(f"invalid JSON from model: {exc}") from exc
        return LLMResult(text=text, data=data, provider=self.name, model=model, **self._usage(response, started))

    def complete_text(self, *, model, system, messages, max_tokens, effort) -> LLMResult:
        started = time.monotonic()
        contents = [
            self.types.Content(role="model" if m["role"] == "assistant" else "user",
                               parts=[self.types.Part(text=m["content"])])
            for m in messages
        ]
        response = self.client.models.generate_content(
            model=model, contents=contents,
            config=self.types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens),
        )
        return LLMResult(text=response.text or "", data=None, provider=self.name, model=model,
                         **self._usage(response, started))
