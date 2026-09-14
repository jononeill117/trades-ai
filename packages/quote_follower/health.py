"""Selector-health checks for the browser quote source."""

from __future__ import annotations

from .sources import DEFAULT_SELECTORS


def checks(mode: str) -> list[dict]:
    return [{
        "name": "quote-follower/quotes-list:fixture",
        "html": "fixtures/quote_portal/quotes.html",
        "patterns": {"quote-row": DEFAULT_SELECTORS["row"],
                     "quote-cell": DEFAULT_SELECTORS["cell"]},
    }]
