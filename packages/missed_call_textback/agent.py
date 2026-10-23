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

from packages.dispatch import desktop_fallback
from packages.dispatch.mock_portal_server import serve, serve_in_sandbox
from packages.dispatch.models import NeedsDesktopError, WorkOrder
from packages.dispatch.portals import get_adapter
from packages.dispatch.schedule import (load_availability, propose_slots,
                                        record_booking)

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
    portal_url = ""
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
            # Send from the payload dict (WS7 T-1 fix-up): an approver
            # edit is copied back into this dict by the gate, and the send
            # below uses exactly what was approved.
            textback_payload = {"to": call.from_number, "text": first_text,
                                "call_id": call.id}
            approved = await gate.require(
                "sms.textback", "quo-sms",
                f"Text {call.from_number}: {first_text[:90]}",
                payload=textback_payload,
                requester="missed-call-textback")
            if not approved:
                run_log.step("textback", status="skipped", call_id=call.id,
                             reason="approval denied — no SMS sent")
                state.mark(call.id)
                results["skipped"] += 1
                continue
            res = await QuoNotifier().send(textback_payload["to"],
                                           textback_payload["text"])
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
                portal_url = url

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

            # -- booking: gated, same as the SMS sends — create_job writes a
            # real FSM job the shop will dispatch a technician to.
            # editable=False (WS7 T-1 fix-up): create_job proceeds from the
            # order/slot objects, not from payload fields, so an approved
            # edit could never reach it. The gate refuses 'e' with an
            # explanation and denies — fix the inputs upstream instead.
            booking_payload = {"call_id": call.id, "from": call.from_number,
                               "caller_name": call.caller_name,
                               "site": order.site_address, "trade": q.trade,
                               "priority": q.urgency,
                               "slot": {"tech": slot.tech, "start": slot.start,
                                        "end": slot.end}}
            approved = await gate.require(
                "booking.create", "portal",
                f"Book {q.trade or 'job'} for {call.caller_name or call.from_number} "
                f"@ {order.site_address} — {slot.label()}",
                payload=booking_payload,
                requester="missed-call-textback", editable=False)
            if not approved:
                run_log.step("book", status="skipped", call_id=call.id,
                             reason="approval denied — no job created")
                state.mark(call.id)
                results["skipped"] += 1
                continue

            # -- book + confirm in the portal (cloud browser) ------------------
            # T-2 standard (WS7 follow-up): creating the job is not booking
            # it. The booked count and the "You're booked" SMS both require
            # the portal's own confirmation — confirm_booking's read-back
            # status must start with "booked". A GUI-only confirm defers to
            # the desktop fallback after the browser session closes, exactly
            # as in dispatch; an unverified fallback result stays pending
            # and is never counted.
            session, page = await core.browser(
                profile_name=cfg.get("portal_profile"), recording=True)
            run_log.session("browser", session.id)
            t0 = time.monotonic()
            needs_desktop = False
            status = None
            try:
                driver = as_driver(page)
                await adapter.login(driver)
                job_ref = await adapter.create_job(driver, order, slot)
                entry["job_ref"] = job_ref
                run_log.step("book", call_id=call.id, ref=job_ref,
                             slot=slot.label())
                try:
                    status = await adapter.confirm_booking(driver, job_ref)
                except NeedsDesktopError:
                    # GUI-only step -> deferred to desktop fallback below,
                    # after the browser session is closed.
                    needs_desktop = True
                entry["job_status"] = status
            finally:
                await session.close()
                run_log.usage("browser", session.id, time.monotonic() - t0)
                replay = await core.replay_url(session.id)
                run_log.session("browser", session.id, replay_url=replay)
                entry["replay_url"] = replay
            if needs_desktop:
                run_log.step("confirm", status="would", call_id=call.id,
                             via="desktop-fallback")
                board_url = job_ref.rstrip("/") + "/board"
                status = await desktop_fallback.confirm_on_dispatch_board(
                    core, run_log, board_url, portal_url)
                entry["job_status"] = status
            run_log.step("confirm", call_id=call.id,
                         status="ok" if str(status).startswith("booked")
                         else "error",
                         job_status=status)
            booked = str(status).startswith("booked")
            if entry.get("job_ref"):
                # The job now exists in the portal and holds this slot:
                # record it into the run's availability so a later call in
                # this same run is not proposed the very same slot (T-2).
                record_booking(availability, slot, entry["job_ref"])

            # -- confirmation SMS (gated; portal-confirmed bookings only) -----
            if booked:
                confirm_text = (f"You're booked for {slot.label()}. "
                                f"Reply here if anything changes.")
                # Send from the payload dict, same contract as the first SMS.
                confirm_payload = {"to": call.from_number, "text": confirm_text,
                                   "call_id": call.id,
                                   "job_ref": entry.get("job_ref"),
                                   "job_status": status}
                approved = await gate.require(
                    "sms.booking_confirm", "quo-sms",
                    f"Text {call.from_number}: {confirm_text[:90]}",
                    payload=confirm_payload,
                    requester="missed-call-textback")
                if approved:
                    res = await QuoNotifier().send(confirm_payload["to"],
                                                   confirm_payload["text"])
                    run_log.step("confirm_sms", status=res.status,
                                 to=call.from_number)
                else:
                    run_log.step("confirm_sms", status="skipped",
                                 reason="approval denied — booking stands, no SMS")
            else:
                run_log.step("confirm_sms", status="skipped", call_id=call.id,
                             reason=f"booking not portal-confirmed "
                                    f"(status: {status}) — no SMS")

            state.mark(call.id)
            results["processed"] += 1
            if booked:
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
