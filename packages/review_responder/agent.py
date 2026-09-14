"""Review responder — new reviews in, approved replies out, weekly digest.

Pipeline:
    poll reviews -> normalize untrusted text (sandbox) -> draft replies
    (pluggable provider, owner voice from config) -> approval gate
    -> publish (cloud browser where no API exists) -> weekly digest

Solari primitives: SANDBOX for untrusted review text (a review is arbitrary
stranger-written content — it gets normalized in a microVM, never parsed by
the orchestrator), and CLOUD BROWSER for the publish step where the platform
has no API.

Never auto-publishes: 1–3 star reviews route to the human-edit lane; 4–5
star drafts take the faster lane — both still require an approval decision.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core import trust as trust_mod
from core.approvals import get_gate
from core.audit import RunLog
from core.config import load_yaml, repo_root
from core.drivers import as_driver
from core.notify import GmailNotifier, SlackNotifier

from .draft import draft_reply, get_provider
from .models import Review
from .normalize_worker import MARKER
from .publish import ReviewPortalAdapter
from ..missed_call_textback.state import ProcessedState

WORKER = Path(__file__).parent / "normalize_worker.py"


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "review_responder.yaml")


async def _load_reviews(core, cfg: dict, run_log: RunLog) -> list[Review]:
    """Read raw reviews, then normalize the untrusted text in a sandbox."""
    path = repo_root() / cfg.get("reviews", "fixtures/reviews/reviews.json")
    raw = json.loads(path.read_text())
    run_log.step("ingest", count=len(raw), source=str(cfg.get("reviews")))
    t0 = time.monotonic()
    stdout = await core.run_python_in_sandbox(
        str(WORKER), {str(path): "/tmp/reviews.json"})
    run_log.usage("sandbox", "normalize-reviews", time.monotonic() - t0)
    idx = stdout.rfind(MARKER)
    if idx < 0:
        raise RuntimeError(f"normalize worker produced no marker: {stdout!r}")
    return [Review.from_dict(d) for d in json.loads(stdout[idx + len(MARKER):])]


def _weekly_digest(reviews: list[Review]) -> str:
    """Trend digest — counts by star band and average rating."""
    if not reviews:
        return "No reviews this period."
    avg = sum(r.stars for r in reviews) / len(reviews)
    bands = {"5": 0, "4": 0, "1-3": 0}
    for r in reviews:
        bands["5" if r.stars == 5 else "4" if r.stars == 4 else "1-3"] += 1
    return (f"Weekly review digest: {len(reviews)} new reviews, "
            f"avg {avg:.1f} stars — 5★: {bands['5']}, 4★: {bands['4']}, "
            f"1–3★: {bands['1-3']}")


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    mode = cfg.get("mode", "mock")
    gate = get_gate(mode, run_log)
    trust = trust_mod.get_ledger()
    state = ProcessedState(repo_root() / "out" / "review_responder_state.json")
    provider = get_provider(cfg.get("drafting", {}))
    voice = cfg.get("voice", {})

    # Mock-mode boundary: serve the bundled review-portal page through
    # core.browser() so the publish step runs the same driver path as live.
    if mode == "mock" and hasattr(core, "use_pages"):
        core.use_pages({"reviews.example": (repo_root() / "fixtures" /
                        "review_portal" / "review_page.html").read_text()})

    reviews = await _load_reviews(core, cfg, run_log)
    adapter = ReviewPortalAdapter(
        cfg.get("portal_base_url", ""), cfg.get("selectors"))

    results = {"reviews": len(reviews), "drafted": 0, "published": 0,
               "skipped": 0, "denied": 0, "publish_skipped_no_target": 0}
    for review in reviews:
        if state.seen(review.id):
            run_log.step("dedupe", status="skipped", review=review.id)
            results["skipped"] += 1
            continue

        draft = draft_reply(review, voice, provider)
        results["drafted"] += 1
        run_log.step("draft", review=review.id, stars=review.stars,
                     lane=draft.lane, provider=draft.provider,
                     warnings=draft.warnings)

        approved = await trust_mod.require(
            gate, trust, run_log,
            "review.publish", "gbp",
            f"[{draft.lane}] Reply to {review.stars}★ review by "
            f"{review.author}: {draft.text[:100]}",
            payload={"review_id": review.id, "stars": review.stars,
                     "lane": draft.lane, "reply": draft.text,
                     "review_text": review.text[:200]},
            requester="review-responder")
        if not approved:
            run_log.step("publish", status="skipped", review=review.id,
                         reason="approval denied — nothing published")
            state.mark(review.id)
            results["denied"] += 1
            continue

        # No real publish target configured? Say so — never pretend a post
        # happened against a fixture domain in live mode.
        if mode == "live" and "example" in adapter.review_url(review):
            run_log.step("publish", status="skipped", review=review.id,
                         reason="no real review platform configured "
                                "(portal_base_url unset) — approval was granted "
                                "but this run only proves the pipeline up to "
                                "the publish boundary")
            state.mark(review.id)
            results["publish_skipped_no_target"] += 1
            continue

        session, page = await core.browser(
            profile_name=cfg.get("profile"), recording=True)
        run_log.session("browser", session.id)
        t0 = time.monotonic()
        try:
            driver = as_driver(page)
            status = await adapter.publish(driver, review, draft)
        finally:
            await session.close()
            run_log.usage("browser", session.id, time.monotonic() - t0)
            run_log.session("browser", session.id,
                            replay_url=await core.replay_url(session.id))
        run_log.step("publish", review=review.id, detail=status)
        state.mark(review.id)
        results["published"] += 1

    # -- weekly digest ---------------------------------------------------------
    digest = _weekly_digest(reviews)
    res = await SlackNotifier().send(":bar_chart: " + digest)
    run_log.step("digest_slack", status=res.status)
    res = await GmailNotifier().send(
        cfg.get("digest_email", "") or "owner@example.com",
        "Weekly review digest", digest)
    run_log.step("digest_email", status=res.status)

    run_log.finish("ok", **results)
    return results
