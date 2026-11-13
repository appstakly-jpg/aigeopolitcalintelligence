"""Deterministic mock provider for tests and offline evaluation.

`responder(kind, system, user_or_messages, schema_name)` returns the payload to emit; the
default responder echoes nothing useful, so tests inject behaviour explicitly - including a
deliberately "compromised" model that obeys prompt injections, to prove that downstream
code does not trust model output for severity, verification or alerting.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .base import LLMResult

Responder = Callable[[str, str, Any, str], Any]


class MockProvider:
    name = "mock"

    def __init__(self, responder: Responder | None = None):
        self.responder = responder or (lambda kind, system, payload, schema_name: {} if kind == "json" else "")
        self.calls: list[dict] = []

    def complete_json(self, *, model, system, user, schema, schema_name, max_tokens, effort) -> LLMResult:
        self.calls.append({"kind": "json", "system": system, "user": user, "schema_name": schema_name})
        data = self.responder("json", system, user, schema_name)
        text = json.dumps(data, ensure_ascii=False)
        return LLMResult(text=text, data=data, provider=self.name, model=model,
                         input_tokens=len(user) // 4, output_tokens=len(text) // 4)

    def complete_text(self, *, model, system, messages, max_tokens, effort) -> LLMResult:
        self.calls.append({"kind": "text", "system": system, "messages": messages})
        text = self.responder("text", system, messages, "")
        return LLMResult(text=str(text), data=None, provider=self.name, model=model,
                         input_tokens=sum(len(m["content"]) for m in messages) // 4, output_tokens=len(str(text)) // 4)
