"""Reminder sequencing — which touch is due, in what tone, on what channel.

Pure functions over (invoice, state, config, today). Timing, tone, escalation
thresholds, and channel choice are all config — see config/invoice_chaser.yaml.
"""

from __future__ import annotations

from typing import Any

from .models import Invoice, Reminder

DEFAULT_SEQUENCE = [
    {"after_days": 1, "tone": "friendly", "channel": "email",
     "subject": "Invoice {invoice_id} — friendly reminder",
     "body": "Hi {name}, just a nudge that invoice {invoice_id} for "
             "${amount} came due {due}. Happy to resend it or answer "
             "questions. — {shop}"},
    {"after_days": 7, "tone": "firm", "channel": "email",
     "subject": "Invoice {invoice_id} — now {days} days past due",
     "body": "Hi {name}, invoice {invoice_id} for ${amount} is now {days} "
             "days past due. Please let us know if there's a problem — "
             "otherwise we'd appreciate payment this week. — {shop}"},
    {"after_days": 14, "tone": "final", "channel": "sms",
     "subject": "Invoice {invoice_id} — final notice",
     "body": "{shop}: invoice {invoice_id} (${amount}) is {days} days past "
             "due. Please call us today to avoid escalation."},
]


def next_step(inv: Invoice, steps_sent: int, cfg: dict[str, Any], today) -> Reminder | None:
    """The reminder that's due next, or None.

    `steps_sent` is how many reminders this invoice has already had — a retry
    picks up where it left off instead of resending step 1.
    """
    seq = cfg.get("sequence") or DEFAULT_SEQUENCE
    days = inv.days_overdue(today)
    if days <= 0 or inv.status in ("paid", "disputed", "escalated"):
        return None
    due_steps = [i for i, s in enumerate(seq) if days >= s.get("after_days", 0)]
    if not due_steps or steps_sent > due_steps[-1]:
        return None                      # nothing new due yet
    step_idx = due_steps[-1]
    if step_idx < steps_sent:
        return None                      # already sent
    spec = seq[step_idx]
    channel = spec["channel"]
    if channel == "email" and not inv.email:
        channel = "sms" if inv.phone else "none"
    if channel == "sms" and not inv.phone:
        channel = "email" if inv.email else "none"
    if channel == "none":
        return None
    name = inv.customer.split()[0] if inv.customer else "there"
    fmt = {"invoice_id": inv.invoice_id, "amount": f"{inv.amount:,.2f}",
           "due": inv.due, "days": days, "name": name,
           "shop": cfg.get("shop_name", "the shop")}
    return Reminder(invoice_id=inv.invoice_id, step=step_idx + 1,
                    channel=channel,
                    subject=spec["subject"].format(**fmt),
                    body=spec["body"].format(**fmt),
                    tone=spec.get("tone", "neutral"))


def should_escalate(inv: Invoice, steps_sent: int, cfg: dict[str, Any], today) -> bool:
    """Non-responders get flagged for human follow-up once the sequence is
    exhausted and the escalate threshold is met."""
    seq = cfg.get("sequence") or DEFAULT_SEQUENCE
    threshold = int(cfg.get("escalate_after_days", 30))
    return (inv.status not in ("paid", "disputed", "escalated")
            and steps_sent >= len(seq)
            and inv.days_overdue(today) >= threshold)
