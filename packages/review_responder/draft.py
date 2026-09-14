"""Reply drafting — pluggable provider interface + deterministic default.

`ReplyProvider` is the seam: the shipped TemplateProvider needs no model and
no keys, so mock mode and CI stay deterministic. An LLM provider can be added
(e.g. an HTTP endpoint) by implementing `draft(review, voice) -> str` — the
rest of the pipeline (lanes, approvals, publishing) doesn't change.

Owner voice is config, not hardcoded: greeting, sign-off, and tone words come
from config/review_responder.yaml so each deployer sounds like themselves.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from .models import Draft, Review

DEFAULT_TEMPLATES = {
    "5": "Thanks so much, {author}! {praise} — {signoff}",
    "4": "Thanks for the review, {author}! {acknowledge} {praise} — {signoff}",
    "low": "{author}, thank you for the honest feedback. {acknowledge} "
           "We'd like to make this right — please reach us directly at "
           "{contact} so we can sort it out. — {signoff}",
}


class ReplyProvider(Protocol):
    name: str
    def draft(self, review: Review, voice: dict[str, Any]) -> str: ...


class TemplateProvider:
    """Deterministic drafting — no model needed, fully testable."""
    name = "template"

    def draft(self, review: Review, voice: dict[str, Any]) -> str:
        templates = {**DEFAULT_TEMPLATES, **(voice.get("templates") or {})}
        key = "low" if review.stars <= 3 else str(review.stars)
        tmpl = templates.get(key) or templates["low"]
        praise = voice.get("praise_line",
                           "we're glad the work held up")
        acknowledge = voice.get("acknowledge_line",
                                "we hear you on the details")
        text = tmpl.format(
            author=review.author.split()[0] if review.author else "there",
            praise=praise, acknowledge=acknowledge,
            contact=voice.get("contact", "the office"),
            signoff=voice.get("signoff", "the team"),
        )
        return _squash(text)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def get_provider(cfg: dict[str, Any]) -> ReplyProvider:
    """Model selection is pluggable: `provider: template` (default). An
    `http` provider would call a configured endpoint — deliberately not
    shipped; the interface is the contract."""
    name = cfg.get("provider", "template")
    if name == "template":
        return TemplateProvider()
    raise KeyError(f"unknown reply provider {name!r} — implement ReplyProvider "
                   f"or use 'template'")


def draft_reply(review: Review, voice: dict[str, Any],
                provider: ReplyProvider) -> Draft:
    warnings = []
    text = provider.draft(review, voice)
    if review.stars <= 3:
        warnings.append("low-star review — human-edit lane, not the fast lane")
    if len(text) > 500:
        warnings.append("draft over 500 chars")
    return Draft(review_id=review.id, text=text, lane=review.lane,
                 provider=provider.name, warnings=warnings)
