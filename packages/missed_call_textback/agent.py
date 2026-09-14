"""Missed-call textback — missed call in, booked job (or escalation) out.

Pipeline:
    missed-call event -> approval -> first SMS (Quo) -> qualify
    -> book into the field-service portal (cloud browser) or escalate
    -> confirmation SMS (approval) -> audit

Solari primitive: CLOUD BROWSER — the booking step happens in the same
field-service portal UI a dispatcher would use, driven by a Solari cloud
browser (live) or the bundled FieldDesk portal over localhost HTTP (mock).

The first outbound SMS always passes through the core approval gate.
Deployers who can legally send a pre-approved transactional template
unattended can list `sms.textback` under `preapproved_actions` in
config/approvals.yaml — the request and decision are still logged either way.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from core.approvals import get_gate
from core.audit import RunLog
from core.config import load_yaml, repo_root
from core.drivers import as_driver
from core.notify import QuoNotifier, SlackNotifier

from packages.dispatch.mock_portal_server import serve, serve_in_sandbox
from packages.dispatch.models import WorkOrder
from packages.dispatch.portals import get_adapter
from packages.dispatch.schedule import load_availability, propose_slots

from .models import MissedCall
from .qualify import qualify
from .state import ProcessedState

from datetime import datetime


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "missed_call_textback.yaml")


def _load_events(cfg: dict) -> list[MissedCall]:
    path = repo_root() / cfg.get("events", "fixtures/missed_calls/events.json")
    return [MissedCall.from_dict(d) for d in json.loads(path.read_text())]


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    mode = cfg.get("mode", "mock")
    gate = get_gate(mode, run_log)
    state = ProcessedState(repo_root() / "out" / "missed_call_state.json")

    events = _load_events(cfg)
    run_log.step("ingest", count=len(events), source=str(cfg.get("events")))

    availability = load_availability(load_yaml(repo_root() / cfg.get(
        "availability", "config/dispatch.availability.yaml")))

    results = {"processed": 0, "booked": 0, "escalated": 0, "skipped": 0}
    keepalive = None
    adapter = None
    try:
        for call in events:
            entry: dict = {"call_id": call.id}
            if state.seen(call.id):
                run_log.step("dedupe", status="skipped", call_id=call.id,
                             reason="already processed — idempotent retry")
                results["skipped"] += 1
                continue

            # -- first outbound SMS: always through the approval gate --------
            first_text = cfg.get("textback_template",
                                 "Sorry we missed your call — this is {shop}. "
                                 "Reply with what you need and we'll get you scheduled.")
            first_text = first_text.replace("{shop}", cfg.get("shop_name", "the shop"))
            approved = await gate.require(
                "sms.textback", "quo-sms",
                f"Text {call.from_number}: {first_text[:90]}",
                payload={"to": call.from_number, "text": first_text,
                         "call_id": call.id},
                requester="missed-call-textback")
            if not approved:
                run_log.step("textback", status="skipped", call_id=call.id,
                             reason="approval denied — no SMS sent")
                state.mark(call.id)
                results["skipped"] += 1
                continue
            res = await QuoNotifier().send(call.from_number, first_text)
            run_log.step("textback", status=res.status, to=call.from_number,
                         detail=res.detail)

            # -- qualify -------------------------------------------------------
            q = qualify(call)
            entry["qualification"] = {"trade": q.trade, "urgency": q.urgency,
                                      "bookable": q.bookable}
            run_log.step("qualify", call_id=call.id, trade=q.trade or "unknown",
                         urgency=q.urgency, bookable=q.bookable)

            if q.escalate:
                res = await SlackNotifier().send(
                    f":telephone_receiver: Missed call needs a human — "
                    f"{call.from_number} ({q.escalate_reason}). "
                    f"Transcript: {call.voicemail_transcript[:140]}")
                run_log.step("escalate", status=res.status, call_id=call.id,
                             reason=q.escalate_reason)
                state.mark(call.id)
                results["escalated"] += 1
                results["processed"] += 1
                continue

            if not q.bookable:
                run_log.step("route", status="skipped", call_id=call.id,
                             reason="not bookable without human follow-up")
                state.mark(call.id)
                results["skipped"] += 1
                continue

            # -- book in the portal (cloud browser) ------------------------------
            if adapter is None:
                if cfg.get("portal", "mock") == "mock":
                    seed = repo_root() / cfg.get("portal_seed", "fixtures/mock_portal/seed.json")
                    if mode == "live":
                        sbx = await core.sandbox()
                        run_log.session("sandbox", sbx.sandboxId)
                        url = await serve_in_sandbox(sbx, seed)
                        keepalive = (sbx, time.monotonic())
                    else:
                        server, url = serve(seed)
                        keepalive = server
                    run_log.step("portal_up", url=url)
                else:
                    url = cfg.get("portal_base_url", "")
                adapter = get_adapter(cfg.get("portal", "mock"), url)

            order = WorkOrder(
                source_id=call.id, customer_name=call.caller_name or call.from_number,
                site_address=cfg.get("default_site", "Address TBD — confirm with customer"),
                tenant_name=call.caller_name, tenant_phone=call.from_number,
                trade=q.trade, priority=q.urgency,
                notes=f"Missed-call textback. {q.summary}",
            )
            slots = propose_slots(order, availability, now=datetime.now())
            if not slots:
                run_log.step("schedule", status="skipped", reason="no open slot")
                state.mark(call.id)
                results["skipped"] += 1
                continue
            slot = slots[0]

            session, page = await core.browser(
                profile_name=cfg.get("portal_profile"), recording=True)
            run_log.session("browser", session.id)
            t0 = time.monotonic()
            try:
                driver = as_driver(page)
                await adapter.login(driver)
                job_ref = await adapter.create_job(driver, order, slot)
                entry["job_ref"] = job_ref
                run_log.step("book", call_id=call.id, ref=job_ref,
                             slot=slot.label())
            finally:
                await session.close()
                run_log.usage("browser", session.id, time.monotonic() - t0)
                replay = await core.replay_url(session.id)
                run_log.session("browser", session.id, replay_url=replay)
                entry["replay_url"] = replay

            # -- confirmation SMS (also gated) ----------------------------------
            confirm_text = (f"You're booked for {slot.label()}. "
                            f"Reply here if anything changes.")
            approved = await gate.require(
                "sms.booking_confirm", "quo-sms",
                f"Text {call.from_number}: {confirm_text[:90]}",
                payload={"to": call.from_number, "text": confirm_text,
                         "call_id": call.id, "job_ref": entry.get("job_ref")},
                requester="missed-call-textback")
            if approved:
                res = await QuoNotifier().send(call.from_number, confirm_text)
                run_log.step("confirm_sms", status=res.status, to=call.from_number)
            else:
                run_log.step("confirm_sms", status="skipped",
                             reason="approval denied — booking stands, no SMS")

            state.mark(call.id)
            results["processed"] += 1
            results["booked"] += 1

        run_log.finish("ok", **results)
        return results
    finally:
        if keepalive is not None:
            if isinstance(keepalive, tuple):
                sbx, t0 = keepalive
                await sbx.kill()
                run_log.usage("sandbox", sbx.sandboxId, time.monotonic() - t0)
            else:
                keepalive.shutdown()
