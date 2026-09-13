"""Procurement agent — parts list in, cheapest compliant quote out.

Pipeline:
    load parts -> price-check every supplier (parallel cloud browsers)
    -> aggregate in a sandbox -> render quote -> deliver (Slack + Gmail)
"""

from __future__ import annotations

from pathlib import Path

from core.audit import RunLog
from core.config import env, load_yaml, repo_root
from core.notify import GmailNotifier, SlackNotifier

from .aggregate import aggregate_in_sandbox
from .fetch import fetch_all
from .parts_list import load_parts
from .report import render_markdown, write_reports
from .suppliers import load_suppliers, supplier_config


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "procurement.yaml")


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    mode = cfg.get("mode", "mock")

    # -- input ----------------------------------------------------------------
    parts = load_parts(repo_root() / cfg.get("parts_list", "fixtures/parts_list.csv"))
    run_log.step("load_parts", count=len(parts))

    # -- price-check (parallel browsers) ---------------------------------------
    suppliers = load_suppliers()
    profiles = {s.name: supplier_config(s.name).get("profile") for s in suppliers}
    offers = await fetch_all(
        core, suppliers, parts, run_log, mode=mode,
        fixtures_dir=repo_root() / "fixtures" / "supplier_pages",
        profiles=profiles,
    )
    run_log.step("price_check", offers=len(offers),
                 priced=sum(1 for o in offers if o.unit_price is not None),
                 gated=sum(1 for o in offers if o.login_required))

    # -- aggregate (sandbox boundary) -------------------------------------------
    baseline = cfg.get("baseline_supplier", "ferguson")
    quote = await aggregate_in_sandbox(core, parts, offers, baseline)
    run_log.step("aggregate", total=quote["total"], savings=quote["savings"])

    # -- report ------------------------------------------------------------------
    md_path, html_path = write_reports(quote, run_log.run_id, repo_root() / "out")
    run_log.step("report", markdown=str(md_path), html=str(html_path))

    # -- deliver ------------------------------------------------------------------
    md = render_markdown(quote, run_log.run_id)
    slack = SlackNotifier()
    res = await slack.send(":shopping_cart: New parts quote\n```\n" + md + "\n```")
    run_log.step("notify_slack", status=res.status, detail=res.detail)

    gmail = GmailNotifier()
    res = await gmail.send(
        cfg.get("quote_email_to", "") or "office@example.com",
        f"Parts quote — total ${quote['total']:,.2f} (save ${quote['savings']:,.2f})",
        md,
        sender=env("DISPATCH_REPLY_FROM") or env("GMAIL_USER"),
    )
    run_log.step("notify_email", status=res.status, detail=res.detail)

    run_log.finish("ok", total=quote["total"], savings=quote["savings"])
    return {"quote": quote, "markdown": str(md_path), "html": str(html_path)}
