"""Quote builder — plain-English job description in, a complete quote out.

Pipeline:
    load job + quote history -> match items (keywords -> historical item
    keys; uncertain matches flagged for human selection) -> assemble in
    the sandbox (each line priced at the median of its historical band,
    clamped inside it, citing the jobs behind it) -> render the full
    quote document -> approval gate -> deliver by email

Solari primitive: SANDBOX — untrusted job text is processed and all
arithmetic runs in a disposable VM. The worker's only numbers come from
the shop's own quote history.

The quote is never a bare price list: it carries explicit inclusions AND
exclusions, exploratory areas (what can't be known until work starts),
plain-spoken concerns/risks, an escalation & scope-increase policy, and
deposit/payment/validity terms.

Idempotent: an unchanged quote for the same job is not re-delivered.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path

from core import trust as trust_mod
from core.approvals import get_gate
from core.audit import RunLog
from core.config import load_yaml, repo_root
from core.notify import GmailNotifier

from .history import (concerns_for, exploratory_for, load_history,
                      match_description)
from .quote_worker import MARKER
from .render import quote_html, quote_md

WORKER = Path(__file__).parent / "quote_worker.py"
STATE = "out/quote_builder_state.json"


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "quote_builder.yaml")


def _load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {"delivered": {}}


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    mode = cfg.get("mode", "mock")
    gate = get_gate(mode, run_log)
    trust = trust_mod.get_ledger()
    state_path = repo_root() / STATE
    state = _load_state(state_path)

    hist_dir = repo_root() / cfg.get(
        "history", "packages/quote_builder/fixtures/history")
    job_path = repo_root() / cfg.get("job", "fixtures/quote_jobs/job-rivera.json")
    job = json.loads(job_path.read_text())
    bands, history_jobs = load_history(hist_dir)
    run_log.step("ingest", job=job.get("job_id"), history_jobs=len(history_jobs),
                 priced_items=len(bands))

    # -- match: plain-English -> historical item keys --------------------------
    description = job.get("description", "")
    matched, uncertain = match_description(
        description, bands, explicit_qty=job.get("quantities"))
    run_log.step("match", matched=[m["item"] for m in matched],
                 uncertain=[u["item"] for u in uncertain])

    # -- assemble in the sandbox --------------------------------------------------
    selection = {
        "matched": matched, "uncertain": uncertain,
        "tax_rate": float(cfg.get("tax_rate", 0)),
        "inclusions": cfg.get("inclusions", []),
        "exclusions": cfg.get("exclusions", []),
        "exploratory": exploratory_for(matched, history_jobs, description),
        "concerns": concerns_for(matched, history_jobs, description),
        "terms": cfg.get("terms", {}),
    }
    bands_json = {k: {"description": b.description, "unit": b.unit,
                      "prices": b.prices, "refs": b.refs}
                  for k, b in bands.items()}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(selection, f)
        sel_path = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(bands_json, f)
        bands_path = f.name
    t0 = time.monotonic()
    stdout = await core.run_python_in_sandbox(str(WORKER), {
        str(job_path): "/tmp/job.json",
        sel_path: "/tmp/selection.json",
        bands_path: "/tmp/bands.json",
    })
    run_log.usage("sandbox", "quote-assemble", time.monotonic() - t0)
    idx = stdout.rfind(MARKER)
    if idx < 0:
        raise RuntimeError(f"quote worker produced no marker: {stdout!r}")
    quote = json.loads(stdout[idx + len(MARKER):])
    run_log.step("assemble", lines=len(quote["lines"]), total=quote["total"],
                 exclusions=len(quote["exclusions"]),
                 exploratory=len(quote["exploratory"]))

    # -- render -------------------------------------------------------------------
    md = quote_md(quote)
    out_dir = repo_root() / "out"
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / f"quote-{job.get('job_id','x')}-{run_log.run_id}.md"
    html_path = md_path.with_suffix(".html")
    md_path.write_text(md)
    html_path.write_text(quote_html(quote))
    run_log.step("render", markdown=str(md_path), html=str(html_path))

    # -- idempotency: an unchanged quote for this job is not re-sent -------------
    fingerprint = hashlib.sha256(md.encode()).hexdigest()[:16]
    job_key = job.get("job_id", "x")
    prior = state["delivered"].get(job_key)
    if prior and prior.get("fingerprint") == fingerprint and prior.get("delivered"):
        run_log.step("deliver", status="skipped",
                     reason="identical quote already delivered for "
                            f"{job_key} — no duplicate send")
        run_log.finish("ok", total=quote["total"], delivered=False,
                       duplicate=True)
        return {"quote": quote, "markdown": str(md_path), "delivered": False,
                "duplicate": True}

    # -- approval + delivery -------------------------------------------------------
    to = job.get("email") or cfg.get("fallback_email", "customer@example.com")
    approved = await trust_mod.require(
        gate, trust, run_log,
        "quote.deliver", "gmail",
        f"Deliver quote {job.get('job_id')} to {to} — total ${quote['total']:,.2f} "
        f"({len(quote['lines'])} lines, {len(uncertain)} unconfirmed)",
        payload={"to": to, "total": quote["total"],
                 "items": [l.get("item") for l in quote["lines"]],
                 "unconfirmed": [u["item"] for u in uncertain]},
        requester="quote-builder")
    if not approved:
        run_log.step("deliver", status="skipped",
                     reason="approval denied — quote rendered but not sent")
    else:
        res = await GmailNotifier().send(
            to, f"Quote {job.get('job_id')} — ${quote['total']:,.2f}", md)
        run_log.step("deliver", status=res.status, to=to)

    state["delivered"][job_key] = {"fingerprint": fingerprint,
                                   "delivered": bool(approved)}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))

    run_log.finish("ok", total=quote["total"], delivered=approved)
    return {"quote": quote, "markdown": str(md_path), "delivered": approved}
