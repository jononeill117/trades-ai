"""Quote builder — job details + photos in, priced quote out.

Pipeline:
    load job + pricebook -> match items (keywords -> item IDs; uncertain
    matches flagged for human selection) -> assemble quote (sandbox:
    pricebook prices only, never model-invented) -> render -> approval gate
    -> deliver by email

Solari primitive: SANDBOX — extraction and total assembly run in a
disposable VM on untrusted job text. The worker's only numbers come from the
pricebook CSV.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from core.approvals import get_gate
from core.audit import RunLog
from core.config import load_yaml, repo_root
from core.notify import GmailNotifier

from .pricebook import load_pricebook, match_items
from .quote_worker import MARKER
from .render import quote_html, quote_md

WORKER = Path(__file__).parent / "quote_worker.py"


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "quote_builder.yaml")


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    mode = cfg.get("mode", "mock")
    gate = get_gate(mode, run_log)

    book_path = repo_root() / cfg.get("pricebook", "fixtures/pricebook/pricebook.csv")
    job_path = repo_root() / cfg.get("job", "fixtures/quote_jobs/job-rivera.json")
    job = json.loads(job_path.read_text())
    pricebook = load_pricebook(book_path)
    run_log.step("ingest", job=job.get("job_id"), pricebook_items=len(pricebook))

    matched, uncertain = match_items(
        job.get("description", ""), pricebook,
        explicit_qty=job.get("quantities"))
    run_log.step("match", matched=[m["item_id"] for m in matched],
                 uncertain=[u["item_id"] for u in uncertain])

    # -- assemble in the sandbox --------------------------------------------------
    selection = {
        "matched": matched, "uncertain": uncertain,
        "tax_rate": float(cfg.get("tax_rate", 0)),
        "assumptions": cfg.get("assumptions", [
            "Standard install access; no code upgrades unless listed"]),
        "exclusions": cfg.get("exclusions", ["Permits unless listed",
                                             "Drywall/paint repair"]),
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(selection, f)
        sel_path = f.name
    t0 = time.monotonic()
    stdout = await core.run_python_in_sandbox(str(WORKER), {
        str(job_path): "/tmp/job.json",
        sel_path: "/tmp/selection.json",
        str(book_path): "/tmp/pricebook.csv",
    })
    run_log.usage("sandbox", "quote-assemble", time.monotonic() - t0)
    idx = stdout.rfind(MARKER)
    quote = json.loads(stdout[idx + len(MARKER):])
    run_log.step("assemble", lines=len(quote["lines"]), total=quote["total"])

    # -- render -------------------------------------------------------------------
    md = quote_md(quote)
    out_dir = repo_root() / "out"
    md_path = out_dir / f"quote-{job.get('job_id','x')}-{run_log.run_id}.md"
    html_path = md_path.with_suffix(".html")
    md_path.write_text(md)
    html_path.write_text(quote_html(quote))
    run_log.step("render", markdown=str(md_path), html=str(html_path))

    # -- approval + delivery --------------------------------------------------------
    to = job.get("email") or cfg.get("fallback_email", "customer@example.com")
    approved = await gate.require(
        "quote.deliver", "gmail",
        f"Deliver quote {job.get('job_id')} to {to} — total ${quote['total']:,.2f} "
        f"({len(quote['lines'])} lines, {len(uncertain)} unconfirmed)",
        payload={"to": to, "total": quote["total"],
                 "items": [l.get("item_id") for l in quote["lines"]],
                 "unconfirmed": [u["item_id"] for u in uncertain]},
        requester="quote-builder")
    if not approved:
        run_log.step("deliver", status="skipped",
                     reason="approval denied — quote rendered but not sent")
    else:
        res = await GmailNotifier().send(
            to, f"Quote {job.get('job_id')} — ${quote['total']:,.2f}", md)
        run_log.step("deliver", status=res.status, to=to)

    run_log.finish("ok", total=quote["total"], delivered=approved)
    return {"quote": quote, "markdown": str(md_path), "delivered": approved}
