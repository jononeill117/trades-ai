"""Quote follower — unsent and idle quotes in, follow-ups out.

Pipeline:
    fetch quotes (CSV baseline, or browser adapter for API-less systems)
    -> classify (never_sent vs sent_inactive) -> alert owner in Slack
    -> follow-up message -> approval gate -> send -> track outcome

Solari primitive: CLOUD BROWSER — when the FSM/CRM has no API, the quotes
list is read through a cloud browser session (recorded, replayable). Mock
mode serves a fixture page through the same driver path.

Attribution honesty: we report quote value, days idle, activity, and that a
follow-up was sent. We do NOT claim a quote was won because we followed up —
outcomes come only from supplied status events.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from core.approvals import get_gate
from core.audit import RunLog
from core.config import load_yaml, repo_root
from core.drivers import as_driver
from core.notify import GmailNotifier, QuoNotifier, SlackNotifier

from .models import Quote
from .sources import BrowserQuoteSource, CsvQuoteSource


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "quote_follower.yaml")


async def _fetch_quotes(core, cfg: dict, run_log: RunLog) -> list[Quote]:
    source = cfg.get("source", "browser")
    if source == "csv":
        quotes = await CsvQuoteSource(
            repo_root() / cfg.get("quotes_csv", "fixtures/quotes/quotes.csv")
        ).fetch_quotes()
        run_log.step("ingest", count=len(quotes), source="csv")
        return quotes

    # Live mode with no real target configured: fall back to the CSV export
    # and say so — never scrape a fixture domain and call it real.
    list_url = cfg.get("list_url", "https://quotes.example/quotes")
    if cfg.get("mode") == "live" and "example" in list_url:
        run_log.step("ingest", status="skipped", source="browser",
                     reason="no real FSM/CRM configured (list_url is the "
                            "fixture domain) — falling back to CSV source")
        quotes = await CsvQuoteSource(
            repo_root() / cfg.get("quotes_csv", "fixtures/quotes/quotes.csv")
        ).fetch_quotes()
        run_log.step("ingest", count=len(quotes), source="csv")
        return quotes

    if mode_is_mock(cfg) and hasattr(core, "use_pages"):
        core.use_pages({"quotes.example": (repo_root() /
                        "fixtures/quote_portal/quotes.html").read_text()})
    session, page = await core.browser(
        profile_name=cfg.get("profile"), recording=True)
    run_log.session("browser", session.id)
    t0 = time.monotonic()
    try:
        driver = as_driver(page)
        src = BrowserQuoteSource(driver, list_url, cfg.get("selectors"))
        quotes = await src.fetch_quotes()
    finally:
        await session.close()
        run_log.usage("browser", session.id, time.monotonic() - t0)
        run_log.session("browser", session.id,
                        replay_url=await core.replay_url(session.id))
    # List pages rarely expose contact/activity columns — join the CSV export
    # (same quote_ids) for email/phone/dates when one is configured.
    import csv as _csv
    csv_path = repo_root() / cfg.get("quotes_csv", "")
    if csv_path.exists():
        by_id = {q.quote_id: q for q in
                 [Quote.from_row(r) for r in
                  _csv.DictReader(csv_path.read_text().splitlines())]}
        for q in quotes:
            full = by_id.get(q.quote_id)
            if full:
                q.email, q.phone = full.email, full.phone
                q.created = q.created or full.created
                q.last_activity = q.last_activity or full.last_activity
    run_log.step("ingest", count=len(quotes), source="browser")
    return quotes


def mode_is_mock(cfg: dict) -> bool:
    return cfg.get("mode", "mock") == "mock"


def _followup_text(q: Quote, cfg: dict) -> tuple[str, str]:
    subject = f"Still interested? Quote {q.quote_id}"
    body = (f"Hi {q.customer.split()[0] if q.customer else 'there'}, "
            f"we put together quote {q.quote_id} for "
            f"${q.amount:,.2f} and haven't heard back — want us to get it "
            f"scheduled or answer any questions? — {cfg.get('shop_name', 'the shop')}")
    return subject, body


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    mode = cfg.get("mode", "mock")
    gate = get_gate(mode, run_log)
    today = date.today()
    idle_days = int(cfg.get("idle_days", 5))

    quotes = await _fetch_quotes(core, cfg, run_log)

    flagged = []
    for q in quotes:
        bucket = q.bucket(today, idle_days)
        run_log.step("classify", quote=q.quote_id, bucket=bucket,
                     days_idle=q.days_idle(today), amount=q.amount)
        if bucket != "active":
            flagged.append((q, bucket))

    # -- alert the owner --------------------------------------------------------
    if flagged:
        total = sum(q.amount for q, _ in flagged)
        lines = [f"  {q.quote_id} — {q.customer} — ${q.amount:,.2f} — "
                 f"{b.replace('_', ' ')}, {q.days_idle(today)}d idle"
                 for q, b in flagged]
        res = await SlackNotifier().send(
            f":money_with_wings: {len(flagged)} quotes need follow-up "
            f"(${total:,.2f} on the table)\n" + "\n".join(lines))
        run_log.step("alert_owner", status=res.status, flagged=len(flagged),
                     value=total)

    results = {"quotes": len(quotes), "flagged": len(flagged),
               "followed_up": 0, "denied": 0}
    for q, bucket in flagged:
        subject, body = _followup_text(q, cfg)
        to = q.email or q.phone
        channel = "email" if q.email else "sms"
        if not to:
            run_log.step("followup", status="skipped", quote=q.quote_id,
                         reason="no contact channel")
            continue
        approved = await gate.require(
            f"quote.followup.{channel}", channel,
            f"Follow up {bucket.replace('_', ' ')} quote {q.quote_id} "
            f"(${q.amount:,.2f}, {q.days_idle(today)}d idle) to {q.customer}",
            payload={"quote_id": q.quote_id, "bucket": bucket, "to": to,
                     "subject": subject, "body": body},
            requester="quote-follower")
        if not approved:
            run_log.step("followup", status="skipped", quote=q.quote_id,
                         reason="approval denied")
            results["denied"] += 1
            continue
        if channel == "email":
            res = await GmailNotifier().send(to, subject, body)
        else:
            res = await QuoNotifier().send(to, body)
        run_log.step("followup", status=res.status, quote=q.quote_id,
                     channel=channel, bucket=bucket)
        if res.status != "error":
            results["followed_up"] += 1

    run_log.finish("ok", **results)
    return results
