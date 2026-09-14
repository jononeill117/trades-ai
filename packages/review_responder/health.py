"""Selector-health checks for the review-portal adapter."""

from __future__ import annotations


def checks(mode: str) -> list[dict]:
    return [{
        "name": "review-responder/review-portal:fixture",
        "html": "fixtures/review_portal/review_page.html",
        "selectors": ["#reply-text", "#reply-submit"],
    }]
