"""The notifier interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SendResult:
    status: str           # "sent" | "skipped" | "error"
    channel: str
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Notifier(Protocol):
    channel: str

    def configured(self) -> bool: ...
    async def send(self, *args: Any, **kwargs: Any) -> SendResult: ...
