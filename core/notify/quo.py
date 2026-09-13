"""Quo SMS — tenant/customer text messages through the Quo virtual phone system.

Quo (formerly OpenPhone) exposes a REST API for sending SMS/MMS from your
business number. This adapter is a documented stub: it implements the
interface and shows the request shape, but the exact endpoint/schema should be
checked against Quo's current API docs (https://www.quo.com) before going live
— the API surface has changed between versions.

Env vars: QUO_API_KEY, QUO_FROM_NUMBER (your Quo number, e.g. +15551234567).
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

from ..config import env
from .base import SendResult

# Check Quo's current docs — the messages endpoint path has varied across
# versions of the API.
QUO_MESSAGES_URL = "https://api.quo.com/v1/messages"


class QuoNotifier:
    channel = "quo-sms"

    def __init__(self, api_key: str | None = None, from_number: str | None = None):
        self.api_key = api_key or env("QUO_API_KEY")
        self.from_number = from_number or env("QUO_FROM_NUMBER")

    def configured(self) -> bool:
        return bool(self.api_key and self.from_number)

    async def send(self, to: str, text: str) -> SendResult:
        if not self.configured():
            return SendResult(
                "skipped", self.channel,
                f"QUO_* not set — would text {to}: {text[:100]}",
            )
        # Stub: verify against current Quo API docs before relying on this.
        body = json.dumps({
            "from": self.from_number,
            "to": [to],
            "content": text,
        }).encode()
        req = urllib.request.Request(
            QUO_MESSAGES_URL, data=body,
            headers={
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
        )
        try:
            res = await asyncio.to_thread(urllib.request.urlopen, req)
            return SendResult("sent", self.channel, f"HTTP {res.status} to {to}")
        except Exception as exc:
            return SendResult("error", self.channel, str(exc))
