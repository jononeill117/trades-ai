"""Data shapes for the review-responder pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Review:
    id: str
    author: str
    stars: int
    ts: str = ""
    text: str = ""
    job_ref: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Review":
        return cls(id=d["id"], author=d.get("author", "customer"),
                   stars=int(d.get("stars", 0)), ts=d.get("ts", ""),
                   text=d.get("text", ""), job_ref=d.get("job_ref", ""))

    @property
    def lane(self) -> str:
        """Approval lane: low-star reviews route to a human-edit path;
        4-5 star drafts use the faster lane. Both still require approval."""
        return "human_edit" if self.stars <= 3 else "fast"


@dataclass
class Draft:
    review_id: str
    text: str
    lane: str
    provider: str = "template"
    warnings: list[str] = field(default_factory=list)
