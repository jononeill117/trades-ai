"""Slack — posts to an ops channel through an incoming webhook.

Setup (2 minutes): Slack app directory -> "Incoming Webhooks" -> add to your
ops channel -> copy the URL into SLACK_WEBHOOK_URL.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

from ..config import env
from .base import SendResult


class SlackNotifier:
    channel = "slack"

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or env("SLACK_WEBHOOK_URL")

    def configured(self) -> bool:
        return bool(self.webhook_url)

    async def send(self, text: str) -> SendResult:
        if not self.configured():
            return SendResult("skipped", self.channel, f"SLACK_WEBHOOK_URL not set — would post: {text[:120]}")
        body = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            self.webhook_url, data=body, headers={"content-type": "application/json"}
        )
        try:
            res = await asyncio.to_thread(urllib.request.urlopen, req)
            res.read()
            return SendResult("sent", self.channel, f"HTTP {res.status}")
        except Exception as exc:
            return SendResult("error", self.channel, str(exc))
