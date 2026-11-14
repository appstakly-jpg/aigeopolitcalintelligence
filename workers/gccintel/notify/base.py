"""Notification channel interface. Channels report honestly: a channel without credentials
returns `skipped` with the missing setting named - it never pretends to have delivered."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Message:
    subject: str
    text: str
    html: str | None = None
    url: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class DeliveryResult:
    status: str                    # sent | failed | skipped
    provider_message_id: str | None = None
    error: str | None = None
    retryable: bool = False


class NotConfigured(Exception):
    pass


class Channel(Protocol):
    name: str

    def send(self, destination: str, message: Message) -> DeliveryResult: ...
