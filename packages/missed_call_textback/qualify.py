"""Qualification — rule-based first pass over a voicemail transcript.

Deliberately deterministic: the transcript is untrusted text, so we match
keywords instead of letting a model decide. Emergency signals always escalate
to a human — a missed-call bot must never book a gas smell into next Tuesday.
"""

from __future__ import annotations

from .models import MissedCall, Qualification

EMERGENCY_KEYWORDS = [
    "gas smell", "smell gas", "carbon monoxide", "sparking", "sparks",
    "smoke", "flooding", "burst pipe", "sewage", "no heat",
]

TRADE_KEYWORDS = {
    "plumbing": ["water heater", "leak", "leaking", "drain", "clog", "toilet",
                 "faucet", "pipe", "sewer", "sump"],
    "hvac": ["furnace", "ac ", "a/c", "air condition", "heat pump", "thermostat",
             "ac quit", "hvac", "duct"],
    "electrical": ["outlet", "ceiling fan", "fan install", "breaker", "panel",
                   "light fixture", "gfci", "switch", "wiring", "ev charger"],
}

URGENCY_KEYWORDS = ["today", "asap", "emergency", "right away", "urgent",
                    "immediately", "flooding", "leaking"]


def qualify(call: MissedCall) -> Qualification:
    text = (call.voicemail_transcript or "").lower()
    q = Qualification()

    if not text.strip():
        q.escalate = True
        q.escalate_reason = "no voicemail transcript — nothing to qualify"
        q.summary = "Missed call, no details left"
        return q

    matched = [kw for kw in EMERGENCY_KEYWORDS if kw in text]
    q.matched_keywords = matched
    if matched:
        q.urgency = "emergency"
        q.escalate = True
        q.escalate_reason = f"emergency keywords: {', '.join(matched)}"

    for trade, keywords in TRADE_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in text]
        if hits:
            q.trade = trade
            q.matched_keywords += hits
            break

    if not q.trade:
        q.escalate = True
        q.escalate_reason = q.escalate_reason or "couldn't tell which trade"
    if q.urgency != "emergency" and any(kw in text for kw in URGENCY_KEYWORDS):
        q.urgency = "high"

    q.bookable = bool(q.trade) and not q.escalate
    who = call.caller_name or "caller"
    q.summary = f"{who}: {q.trade or 'unknown'} — {text[:80].strip()}"
    return q
