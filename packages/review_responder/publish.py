"""Publish adapter — post a reply to a review through the platform's web UI.

GBP and friends expose a reply form per review; this adapter drives it through
a PageDriver (Solari cloud browser in live mode, fixture page in mock mode).
Selectors live in config so a platform UI change is a config fix.
"""

from __future__ import annotations

from .models import Draft, Review

DEFAULT_SELECTORS = {
    "reply.text": "#reply-text",
    "reply.submit": "#reply-submit",
}


class ReviewPortalAdapter:
    name = "review-portal"

    def __init__(self, base_url: str = "", selectors: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.sel = {**DEFAULT_SELECTORS, **(selectors or {})}

    def review_url(self, review: Review) -> str:
        return f"{self.base_url or 'https://reviews.example'}/reviews/{review.id}"

    async def publish(self, driver, review: Review, draft: Draft) -> str:
        """Submit the reply. Returns a status string."""
        await driver.goto(self.review_url(review))
        await driver.fill(self.sel["reply.text"], draft.text)
        await driver.click(self.sel["reply.submit"])
        # A real portal would return to the review with the reply posted; the
        # fixture page is read-only, so mock mode reports what it attempted.
        return f"submitted reply for {review.id} via {self.review_url(review)}"
