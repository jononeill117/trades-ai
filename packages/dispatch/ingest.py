"""Ingest — where work-order emails come from.

`FixtureInbox` reads bundled sample .eml files (mock mode, tests).
`GmailInbox` polls a real Gmail mailbox over IMAP (live mode) — needs
GMAIL_USER + GMAIL_APP_PASSWORD (a Gmail app password, not your login).

A work-order email is untrusted input: nothing here parses content. That is
the sandbox's job.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
from pathlib import Path
from typing import Protocol

from .models import RawEmail


class Inbox(Protocol):
    def fetch(self) -> list[RawEmail]: ...


class FixtureInbox:
    """Reads every .eml file in a directory. Deterministic — sorted by name."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def fetch(self) -> list[RawEmail]:
        out = []
        from .parse_worker import _body_text

        for path in sorted(self.directory.glob("*.eml")):
            # compat32 keeps raw text — policy.default escapes 8-bit bodies
            msg = email.message_from_string(path.read_text())
            body = _body_text(msg)
            out.append(RawEmail(
                message_id=str(msg.get("message-id", path.name)),
                sender=str(msg.get("from", "")),
                subject=str(msg.get("subject", "")),
                body=body,
                path=str(path),
            ))
        return out


class GmailInbox:
    """Polls a Gmail mailbox over IMAP for unseen work-order emails.

    `subject_filter` narrows the search (e.g. "Work Order"). Fetch only —
    marking messages read is deliberately not done here so a re-run is safe.
    """

    def __init__(self, user: str, app_password: str, subject_filter: str = "Work Order"):
        self.user = user
        self.app_password = app_password
        self.subject_filter = subject_filter

    def fetch(self) -> list[RawEmail]:
        return asyncio.run(self._fetch())

    async def _fetch(self) -> list[RawEmail]:
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> list[RawEmail]:
        out: list[RawEmail] = []
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(self.user, self.app_password)
            imap.select("INBOX")
            crit = f'(UNSEEN SUBJECT "{self.subject_filter}")'
            _, data = imap.search(None, crit)
            for num in (data[0] or b"").split():
                _, fetched = imap.fetch(num, "(RFC822)")
                raw = fetched[0][1]
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body += part.get_content()
                else:
                    body = msg.get_content()
                out.append(RawEmail(
                    message_id=str(msg.get("message-id", num.decode())),
                    sender=str(msg.get("from", "")),
                    subject=str(msg.get("subject", "")),
                    body=body,
                ))
        return out
