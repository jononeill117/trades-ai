"""Gmail — sends plain-text email through Gmail's SMTP endpoint.

Needs a Gmail *app password* (Google Account -> Security -> 2-Step
Verification -> App passwords), never your real password. Set GMAIL_USER and
GMAIL_APP_PASSWORD.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from ..config import env
from .base import SendResult


class GmailNotifier:
    channel = "gmail"

    def __init__(self, user: str | None = None, app_password: str | None = None):
        self.user = user or env("GMAIL_USER")
        self.app_password = app_password or env("GMAIL_APP_PASSWORD")

    def configured(self) -> bool:
        return bool(self.user and self.app_password)

    async def send(self, to: str, subject: str, body: str, sender: str | None = None) -> SendResult:
        if not self.configured():
            return SendResult(
                "skipped", self.channel,
                f"GMAIL_* not set — would email {to}: {subject}",
            )
        msg = EmailMessage()
        msg["From"] = sender or self.user
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        def _send() -> None:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(self.user, self.app_password)
                smtp.send_message(msg)

        try:
            await asyncio.to_thread(_send)
            return SendResult("sent", self.channel, f"emailed {to}")
        except Exception as exc:
            return SendResult("error", self.channel, str(exc))
