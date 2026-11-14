"""Email (SMTP), Slack (incoming webhook), Telegram (Bot API), signed webhook and mock channels."""

from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
import ssl
import time
import uuid
from email.message import EmailMessage
from email.utils import make_msgid

import httpx

from ..config import Settings
from .base import DeliveryResult, Message


class EmailChannel:
    name = "email"

    def __init__(self, settings: Settings):
        self.s = settings

    def send(self, destination: str, message: Message) -> DeliveryResult:
        if not self.s.smtp_host or not self.s.smtp_from:
            return DeliveryResult("skipped", error="email not configured: set SMTP_HOST and SMTP_FROM")
        msg = EmailMessage()
        msg["From"] = self.s.smtp_from
        msg["To"] = destination
        msg["Subject"] = message.subject
        msg_id = make_msgid(domain=self.s.smtp_from.split("@")[-1] if "@" in self.s.smtp_from else None)
        msg["Message-ID"] = msg_id
        msg["X-GCCIntel-Alert"] = message.meta.get("alert_id", "")
        msg.set_content(message.text)
        if message.html:
            msg.add_alternative(message.html, subtype="html")
        try:
            with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=20) as smtp:
                if self.s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if self.s.smtp_username:
                    smtp.login(self.s.smtp_username, self.s.smtp_password or "")
                smtp.send_message(msg)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            return DeliveryResult("failed", error=f"rejected: {exc}"[:300], retryable=False)
        except (OSError, smtplib.SMTPException) as exc:
            return DeliveryResult("failed", error=f"{type(exc).__name__}: {exc}"[:300], retryable=True)
        return DeliveryResult("sent", provider_message_id=msg_id)


class SlackChannel:
    """Destination is an incoming-webhook URL stored as an encrypted tenant secret."""
    name = "slack"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def send(self, destination: str, message: Message) -> DeliveryResult:
        if not destination:
            return DeliveryResult("skipped", error="slack not configured: add the 'slack_webhook_url' tenant secret")
        if not destination.startswith("https://hooks.slack.com/"):
            return DeliveryResult("failed", error="slack webhook URL must start with https://hooks.slack.com/")
        payload = {"text": f"*{message.subject}*\n{message.text}"[:3000], "unfurl_links": False}
        try:
            with httpx.Client(timeout=15, transport=self.transport) as c:
                r = c.post(destination, json=payload)
        except httpx.HTTPError as exc:
            return DeliveryResult("failed", error=f"{type(exc).__name__}: {exc}"[:300], retryable=True)
        if r.status_code == 200:
            return DeliveryResult("sent", provider_message_id=r.headers.get("x-slack-req-id"))
        return DeliveryResult("failed", error=f"HTTP {r.status_code}: {r.text[:200]}", retryable=r.status_code >= 500 or r.status_code == 429)


class TelegramChannel:
    """Destination is a chat id; the bot token comes from TELEGRAM_BOT_TOKEN or a tenant secret."""
    name = "telegram"

    def __init__(self, token: str | None, transport: httpx.BaseTransport | None = None):
        self.token = token
        self.transport = transport

    def send(self, destination: str, message: Message) -> DeliveryResult:
        if not self.token:
            return DeliveryResult("skipped", error="telegram not configured: set TELEGRAM_BOT_TOKEN or tenant secret 'telegram_bot_token'")
        if not destination:
            return DeliveryResult("skipped", error="telegram chat id missing in alert rule destination")
        text = f"{message.subject}\n\n{message.text}"[:4000]
        try:
            with httpx.Client(timeout=15, transport=self.transport) as c:
                r = c.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                           json={"chat_id": destination, "text": text, "disable_web_page_preview": True})
        except httpx.HTTPError as exc:
            return DeliveryResult("failed", error=f"{type(exc).__name__}"[:300], retryable=True)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and data.get("ok"):
            return DeliveryResult("sent", provider_message_id=str(data.get("result", {}).get("message_id")))
        return DeliveryResult("failed", error=f"HTTP {r.status_code}: {str(data.get('description', ''))[:200]}",
                              retryable=r.status_code >= 500 or r.status_code == 429)


class WebhookChannel:
    """Generic JSON webhook signed with HMAC-SHA256 (header X-GCCIntel-Signature: t=<ts>,v1=<hex>)."""
    name = "webhook"

    def __init__(self, signing_secret: str | None, transport: httpx.BaseTransport | None = None):
        self.secret = signing_secret
        self.transport = transport

    def send(self, destination: str, message: Message) -> DeliveryResult:
        if not destination:
            return DeliveryResult("skipped", error="webhook URL missing in alert rule destination")
        if not self.secret:
            return DeliveryResult("skipped", error="webhook not configured: add the 'webhook_signing_secret' tenant secret")
        body = json.dumps({"subject": message.subject, "text": message.text, "url": message.url, **message.meta},
                          ensure_ascii=False).encode()
        ts = str(int(time.time()))
        sig = hmac.new(self.secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        try:
            with httpx.Client(timeout=15, transport=self.transport) as c:
                r = c.post(destination, content=body, headers={"content-type": "application/json",
                                                               "x-gccintel-signature": f"t={ts},v1={sig}"})
        except httpx.HTTPError as exc:
            return DeliveryResult("failed", error=f"{type(exc).__name__}: {exc}"[:300], retryable=True)
        if 200 <= r.status_code < 300:
            return DeliveryResult("sent", provider_message_id=r.headers.get("x-request-id"))
        return DeliveryResult("failed", error=f"HTTP {r.status_code}", retryable=r.status_code >= 500 or r.status_code == 429)


class MockChannel:
    """Records messages in memory; used by tests and by `DEMO_MODE` when no SMTP server is available."""
    name = "mock"

    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []

    def send(self, destination: str, message: Message) -> DeliveryResult:
        self.sent.append((destination, message))
        return DeliveryResult("sent", provider_message_id=f"mock-{uuid.uuid4().hex[:12]}")
