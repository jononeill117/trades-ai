"""Notification adapters — one interface, three channels.

Each notifier answers two questions:
  configured()  — are its env vars set?
  send(...)     — deliver the message, or say what WOULD have been sent.

Nothing here raises on missing config; a use case should still complete and
log "would have sent" when a channel isn't set up.
"""

from .base import Notifier, SendResult
from .slack import SlackNotifier
from .gmail import GmailNotifier
from .quo import QuoNotifier

__all__ = ["Notifier", "SendResult", "SlackNotifier", "GmailNotifier", "QuoNotifier"]
