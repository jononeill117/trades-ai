"""Invoice chaser — aging AR in, escalating reminders out, recovery reported.

Pipeline:
    AR CSV -> normalize (sandbox; untrusted export) -> apply supplied
    payment/status events -> pick next reminder (configurable sequence)
    -> approval gate -> send (email/SMS) -> flag non-responders -> report

Solari primitive: SANDBOX — an AR export is an untrusted file; it is parsed
and normalized inside a disposable VM and only normalized JSON comes back.

Honesty rule: revenue is only "recovered" when a supplied payment/status
event says so. Sending a reminder never counts as recovery.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from core.approvals import get_gate
from core.audit import RunLog
from core.config import load_yaml, repo_root
from core.notify import GmailNotifier, QuoNotifier, SlackNotifier

from .models import Invoice
from .normalize_worker import MARKER
from .sequence import next_step, should_escalate

WORKER = Path(__file__).parent / "normalize_worker.py"


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "invoice_chaser.yaml")


def _load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {"invoices": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _payment_events(cfg: dict) -> dict[str, dict]:
    """Supplied payment/status events — the ONLY source of 'paid'."""
    path = cfg.get("payments")
    if not path:
        return {}
    p = repo_root() / path
    if not p.exists():
        return {}
    return {e["invoice_id"]: e for e in json.loads(p.read_text())}


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    mode = cfg.get("mode", "mock")
    gate = get_gate(mode, run_log)
    today = date.today()
    state_path = repo_root() / "out" / "invoice_chaser_state.json"
    state = _load_state(state_path)

    # -- ingest + normalize (sandbox boundary) ----------------------------------
    ar_path = repo_root() / cfg.get("aging_csv", "fixtures/ar/aging.csv")
    t0 = time.monotonic()
    stdout = await core.run_python_in_sandbox(
        str(WORKER), {str(ar_path): "/tmp/aging.csv"})
    run_log.usage("sandbox", "normalize-ar", time.monotonic() - t0)
    idx = stdout.rfind(MARKER)
    if idx < 0:
        raise RuntimeError(f"AR worker produced no marker: {stdout!r}")
    invoices = []
    for d in json.loads(stdout[idx + len(MARKER):]):
        inv = Invoice(**{k: v for k, v in d.items()
                         if k in Invoice.__dataclass_fields__})
        invoices.append(inv)
        if inv.warnings:
            run_log.step("normalize", status="skipped", invoice=inv.invoice_id,
                         warnings=inv.warnings)
    run_log.step("ingest", invoices=len(invoices))

    # -- apply supplied payment/status events ------------------------------------
    payments = _payment_events(cfg)
    for inv in invoices:
        ev = payments.get(inv.invoice_id)
        if ev and ev.get("event") in ("paid", "disputed", "promised"):
            prev = inv.status
            inv.status = ev["event"]
            run_log.step("status_event", invoice=inv.invoice_id,
                         old=prev, new=inv.status,
                         source=str(cfg.get("payments")))

    recovered = sum(float(e.get("amount", 0)) for e in payments.values()
                    if e.get("event") == "paid")

    results = {"invoices": len(invoices), "reminded": 0, "escalated": 0,
               "skipped": 0, "recovered_revenue": recovered}
    for inv in invoices:
        inv_state = state["invoices"].setdefault(
            inv.invoice_id, {"steps_sent": 0, "status": "aging"})
        inv_state["status"] = inv.status

        if inv.status in ("paid", "disputed"):
            run_log.step("route", status="skipped", invoice=inv.invoice_id,
                         state=inv.status)
            results["skipped"] += 1
            continue

        if should_escalate(inv, inv_state["steps_sent"], cfg, today):
            if inv_state.get("escalated"):
                run_log.step("escalate", status="skipped",
                             invoice=inv.invoice_id, reason="already flagged")
                continue
            res = await SlackNotifier().send(
                f":rotating_light: Invoice {inv.invoice_id} "
                f"({inv.customer}, ${inv.amount:,.2f}) exhausted the reminder "
                f"sequence — needs a human call.")
            run_log.step("escalate", status=res.status, invoice=inv.invoice_id)
            inv_state["escalated"] = True
            inv.status = "escalated"
            results["escalated"] += 1
            continue

        reminder = next_step(inv, inv_state["steps_sent"], cfg, today)
        if reminder is None:
            run_log.step("route", status="skipped", invoice=inv.invoice_id,
                         reason="no reminder due / no channel")
            results["skipped"] += 1
            continue

        to = inv.email if reminder.channel == "email" else inv.phone
        approved = await gate.require(
            f"invoice.reminder.{reminder.channel}", reminder.channel,
            f"{reminder.tone} reminder to {inv.customer} ({to}) — "
            f"{inv.invoice_id} ${inv.amount:,.2f}, step {reminder.step}",
            payload={"invoice_id": inv.invoice_id, "to": to,
                     "subject": reminder.subject, "body": reminder.body,
                     "tone": reminder.tone},
            requester="invoice-chaser")
        if not approved:
            run_log.step("remind", status="skipped", invoice=inv.invoice_id,
                         reason="approval denied — nothing sent")
            continue

        if reminder.channel == "email":
            res = await GmailNotifier().send(
                to, reminder.subject, reminder.body)
        else:
            res = await QuoNotifier().send(to, reminder.body)
        run_log.step("remind", status=res.status, invoice=inv.invoice_id,
                     step=reminder.step, channel=reminder.channel,
                     tone=reminder.tone)
        if res.status != "error":
            inv_state["steps_sent"] = reminder.step
            inv_state["status"] = "contacted"
            results["reminded"] += 1

    _save_state(state_path, state)
    run_log.step("report", recovered_revenue=recovered,
                 note="recovered = supplied payment events only; "
                      "sending a reminder never counts")
    run_log.finish("ok", **results)
    return results
