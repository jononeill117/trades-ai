"""Work-order parser — runs INSIDE the boundary, never on the host.

This file is deliberately self-contained (standard library only) so it can be
copied verbatim into a Solari sandbox and run there:

    sandbox.files.write("/tmp/parse_worker.py", <this file>)
    sandbox.commands.run("python3", ["/tmp/parse_worker.py", "/tmp/order.eml"])

Raw work-order email is untrusted input — it can say anything, including
"ignore your instructions". It is processed inside a microVM, and only the
normalized JSON it prints (after the @@RESULT@@ marker) crosses back out.

Run directly for tests:  python parse_worker.py order.eml
"""

from __future__ import annotations

import email
import json
import re
import sys

MARKER = "@@RESULT@@"

# --------------------------------------------------------------------------
# Field extraction
# --------------------------------------------------------------------------

_LABELS = {
    "work order": "source_id",
    "work order #": "source_id",
    "wo": "source_id",
    "wo #": "source_id",
    "customer": "customer_name",
    "client": "customer_name",
    "site": "site_address",
    "site address": "site_address",
    "address": "site_address",
    "property": "site_address",
    "tenant": "tenant_raw",
    "tenant contact": "tenant_raw",
    "contact": "tenant_raw",
    "trade": "trade",
    "priority": "priority",
    "sla": "sla_raw",
    "sla window": "sla_raw",
    "window": "sla_raw",
    "notes": "notes",
    "description": "notes",
}

_TRADE_KEYWORDS = [
    ("plumbing", ["plumb", "leak", "drain", "water heater", "toilet", "faucet",
                  "pipe", "sewer", "sink", "clog", "p-trap", "flapper"]),
    ("hvac", ["hvac", "furnace", "air condition", "a/c", "ac unit", "heat pump",
              "thermostat", "duct", "refrigerant", "condenser", "no heat",
              "no cooling"]),
    ("electrical", ["electr", "outlet", "breaker", "panel", "wiring", "circuit",
                    "gfci", "light fixture", "switch", "sparking"]),
]

_EMERGENCY_WORDS = ["flood", "flooding", "gas smell", "sparking", "no heat",
                    "burst pipe", "sewage", "no water", "emergency", "urgent",
                    "immediately", "asap"]
_HIGH_WORDS = ["high", "soon", "leak", "backing up", "not working", "down"]
_LOW_WORDS = ["low", "when convenient", "no rush", "cosmetic"]

_PHONE_RE = re.compile(r"(\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_DATETIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T\s]+(\d{1,2}:\d{2})")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _labeled_fields(body: str) -> dict[str, str]:
    """Pull 'Label: value' lines out of the body. A 'Notes:'/'Description:'
    label swallows the rest of the body (it is almost always last)."""
    fields: dict[str, str] = {}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = re.match(r"^([A-Za-z #/]{2,24}):\s*(.*)$", line)
        if m and m.group(1).strip().lower() in _LABELS:
            key = _LABELS[m.group(1).strip().lower()]
            value = m.group(2).strip()
            if key == "notes":
                rest = [value] + [ln.strip() for ln in lines[i + 1:] if ln.strip()]
                fields[key] = "\n".join(rest).strip()
                break
            fields[key] = value
        i += 1
    return fields


def _split_tenant(raw: str) -> dict[str, str]:
    """'Rosa Delgado — 937-555-0184 — rosa@x.com' -> name/phone/email, in any order."""
    out = {"tenant_name": "", "tenant_phone": "", "tenant_email": ""}
    if not raw:
        return out
    email_m = _EMAIL_RE.search(raw)
    if email_m:
        out["tenant_email"] = email_m.group(0)
        raw = raw.replace(email_m.group(0), " ")
    phone_m = _PHONE_RE.search(raw)
    if phone_m:
        out["tenant_phone"] = phone_m.group(0).strip()
        raw = raw.replace(phone_m.group(0), " ")
    name = re.sub("[—\\-/,|]+", " ", raw).strip()
    out["tenant_name"] = re.sub(r"\s{2,}", " ", name)
    return out


def _normalize_trade(text: str) -> str:
    low = text.lower()
    for trade, words in _TRADE_KEYWORDS:
        if any(w in low for w in words):
            return trade
    return ""


def _normalize_priority(text: str) -> str:
    low = text.lower()
    if any(w in low for w in _EMERGENCY_WORDS):
        return "emergency"
    if "high" in low or any(w in low for w in _HIGH_WORDS):
        return "high"
    if any(w in low for w in _LOW_WORDS):
        return "low"
    return "normal"


def _parse_window(raw: str) -> tuple[str, str]:
    """'2026-09-14 08:00 to 2026-09-16 17:00' -> (start, end), tolerant of
    'to', '-', '–', or missing times."""
    times = _DATETIME_RE.findall(raw)
    if len(times) >= 2:
        return f"{times[0][0]} {times[0][1]}", f"{times[1][0]} {times[1][1]}"
    dates = _DATE_RE.findall(raw)
    if len(dates) >= 2:
        return f"{dates[0]} 08:00", f"{dates[1]} 17:00"
    if len(times) == 1:
        return f"{times[0][0]} {times[0][1]}", ""
    if len(dates) == 1:
        return f"{dates[0]} 08:00", ""
    return "", ""


def _body_text(msg) -> str:
    def _decode(part) -> str:
        cte = (part.get("content-transfer-encoding") or "").lower()
        if cte in ("base64", "quoted-printable"):
            payload = part.get_payload(decode=True)
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
        # No real transfer encoding: keep the raw text as-is.
        payload = part.get_payload()
        if isinstance(payload, bytes):
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
        return payload or ""

    if msg.is_multipart():
        return "".join(_decode(p) for p in msg.walk()
                       if p.get_content_type() == "text/plain")
    return _decode(msg)


def parse_work_order(eml_text: str) -> dict:
    """Raw RFC822 email -> normalized work-order dict. Pure, no I/O.

    Parsed with the compat32 API on purpose: policy.default renders 8-bit
    bodies without a Content-Transfer-Encoding header as literal backslash-u
    escapes, which would mangle real mail."""
    msg = email.message_from_string(eml_text)
    subject = str(msg.get("subject", ""))
    sender = str(msg.get("from", ""))
    message_id = str(msg.get("message-id", ""))
    body = _body_text(msg)

    fields = _labeled_fields(body)
    warnings: list[str] = []

    # Trade/priority: explicit labels win, else infer from subject+notes.
    haystack = " ".join([fields.get("trade", ""), subject, fields.get("notes", "")])
    trade = _normalize_trade(fields.get("trade", "")) or _normalize_trade(haystack)
    priority = _normalize_priority(fields.get("priority", "") or haystack)

    sla_start, sla_end = _parse_window(fields.get("sla_raw", ""))
    tenant = _split_tenant(fields.get("tenant_raw", ""))

    source_id = fields.get("source_id", "")
    if not source_id:
        m = re.search(r"(WO[- ]?\d+)", subject, re.I)
        source_id = m.group(1).replace(" ", "-") if m else message_id or "unknown"

    order = {
        "source_id": source_id,
        "customer_name": fields.get("customer_name", sender.split("<")[0].strip(' "')),
        "site_address": fields.get("site_address", ""),
        "trade": trade,
        "priority": priority,
        "sla_start": sla_start,
        "sla_end": sla_end,
        "notes": fields.get("notes", ""),
        "duration_minutes": 120,
        "raw_message_id": message_id,
        **tenant,
    }

    for required in ("customer_name", "site_address", "trade"):
        if not order[required]:
            warnings.append(f"missing {required}")
    if not sla_start:
        warnings.append("no SLA window parsed — scheduler will use priority default")
    order["needs_review"] = bool(warnings and not order["site_address"]) or "missing trade" in warnings
    order["warnings"] = warnings
    return order


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: parse_worker.py <email.eml>", file=sys.stderr)
        return 2
    with open(argv[1], "r", encoding="utf-8", errors="replace") as fh:
        result = parse_work_order(fh.read())
    print(MARKER + json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
