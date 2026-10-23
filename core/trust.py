"""Progressive trust — training wheels that come off only when earned.

Philosophy: nobody hands customer-facing sends to a new system on day one.
So every gated action type starts at `gate` — a human approves each one.
The trust ledger (`out/trust_ledger.json`) records, per action type, how
those decisions went: attempts, approved unchanged, approved after edits,
denied. Confidence is simply how often the draft went out unchanged.

When an action's confidence crosses the configured bar, the system
**proposes** loosening — a logged event, echoed in run output and posted
to the ops channel — but it never loosens itself. The owner grants or
revokes per action in `config/autonomy.yaml` (`gate` <-> `auto`). `auto`
actions still log fully and are marked `trust_auto` in the run log, and
denials/edits push confidence right back down.

A grant can carry a payload **scope** (`scopes:` in the same config): the
proposal text promises one (5-star replies only, friendly reminders only,
quotes under a ceiling), so the grant must enforce it. An `auto` action
with a scope only auto-runs payloads inside the scope; anything outside
— or anything the scope cannot verify — falls back to the human gate.
That is the promise the owner accepted when they flipped the switch.

See docs/trust.md.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import load_yaml, repo_root

AUTONOMY_CONFIG = "config/autonomy.yaml"
LEDGER_PATH = "out/trust_ledger.json"

OUTCOMES = ("approved", "edited", "denied", "auto")

PROPOSAL_TEMPLATE = (
    "I've drafted {attempts} {label} — you approved {approved} unchanged "
    "({pct:.0%}). Confidence is very high. Proposal: {hint} "
    "You can always check in on me or revoke this in config/autonomy.yaml."
)


class TrustLedger:
    """Per-action-type decision history + owner grants."""

    def __init__(self, path: Path | None = None, cfg: dict | None = None):
        self.path = path or repo_root() / LEDGER_PATH
        self.cfg = cfg if cfg is not None else load_yaml(
            repo_root() / AUTONOMY_CONFIG)
        self.actions: dict[str, dict[str, int]] = {}
        self.proposals: dict[str, dict] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.actions = data.get("actions", {})
                self.proposals = data.get("proposals", {})
            except json.JSONDecodeError:
                pass
        else:
            # First run on this machine: import any seeded decision history
            # (a deployer's record of decisions made before this file existed).
            for action, s in (self.cfg.get("seed") or {}).items():
                self.actions[action] = {
                    "attempts": int(s.get("approved", 0))
                                + int(s.get("edited", 0))
                                + int(s.get("denied", 0)),
                    "approved": int(s.get("approved", 0)),
                    "edited": int(s.get("edited", 0)),
                    "denied": int(s.get("denied", 0)),
                    "auto": int(s.get("auto", 0)),
                }
            self.save()

    # -- owner grants -------------------------------------------------------

    def level(self, action: str) -> str:
        """The owner's current grant for this action: 'gate' or 'auto'.
        Anything not explicitly granted stays gated."""
        return "auto" if (self.cfg.get("levels") or {}).get(action) == "auto" \
            else "gate"

    def scope(self, action: str) -> dict:
        """The payload scope attached to this action's grant
        (config/autonomy.yaml `scopes:`), or {} when the grant is
        unscoped. See `scope_allows` for the rule language."""
        return dict((self.cfg.get("scopes") or {}).get(action) or {})

    # -- recording ----------------------------------------------------------

    def record(self, action: str, outcome: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown trust outcome {outcome!r}")
        a = self.actions.setdefault(
            action, {k: 0 for k in OUTCOMES} | {"attempts": 0})
        a["attempts"] += 1
        a[outcome] += 1
        # A denial or an edit on an open proposal withdraws it — the signal
        # changed, so the offer is stale.
        if outcome in ("denied", "edited") and action in self.proposals:
            del self.proposals[action]

    # -- confidence ----------------------------------------------------------

    def stats(self, action: str) -> dict[str, int]:
        return dict(self.actions.get(action, {k: 0 for k in OUTCOMES}
                                     | {"attempts": 0}))

    def confidence(self, action: str) -> float:
        """Share of human decisions that approved the draft unchanged.
        Edits and denials count against; auto-run actions don't count."""
        s = self.stats(action)
        decided = s["approved"] + s["edited"] + s["denied"]
        return s["approved"] / decided if decided else 0.0

    # -- proposals ------------------------------------------------------------

    def check_proposal(self, action: str) -> dict | None:
        """Fire a loosen-autonomy proposal when confidence crosses the bar.
        Returns the proposal dict once per action until the owner acts or
        the signal regresses (denial/edit withdraws it)."""
        if self.level(action) != "gate" or action in self.proposals:
            return None
        th = self.cfg.get("thresholds") or {}
        s = self.stats(action)
        conf = self.confidence(action)
        if s["attempts"] < int(th.get("min_attempts", 25)) \
                or conf < float(th.get("min_confidence", 0.95)):
            return None
        label = (self.cfg.get("labels") or {}).get(action, f"{action} requests")
        hint = (self.cfg.get("proposals") or {}).get(
            action, f"switch `{action}` to `auto` in config/autonomy.yaml.")
        prop = {
            "action": action,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "attempts": s["attempts"],
            "approved_unchanged": s["approved"],
            "confidence": round(conf, 4),
            "text": PROPOSAL_TEMPLATE.format(
                attempts=s["attempts"], label=label,
                approved=s["approved"], pct=conf, hint=hint),
        }
        self.proposals[action] = prop
        return prop

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"actions": self.actions, "proposals": self.proposals},
            indent=2))


def get_ledger(cfg: dict | None = None, path: Path | None = None) -> TrustLedger:
    return TrustLedger(path=path, cfg=cfg)


def scope_allows(scope: dict | None, payload: dict[str, Any] | None) -> bool:
    """Does this payload fall inside the owner's granted scope?

    Rule language (every rule must hold):
    - ``"field": [v, ...]`` — payload[field] must be one of the values
    - ``"field": v``        — payload[field] must equal v
    - ``"field_max": n``    — payload[field] must be a number <= n
    - ``"field_min": n``    — payload[field] must be a number >= n

    Anything unverifiable fails — a missing field, a non-numeric value
    under a numeric rule, or a rule value of None (e.g. a price ceiling
    the owner has not set yet). Failing scope never denies the action;
    `require` routes it to the human gate instead of auto-running it.
    """
    if not scope:
        return True
    if not isinstance(payload, dict):
        return False
    for key, rule in scope.items():
        if rule is None:
            return False
        if key.endswith("_max") or key.endswith("_min"):
            value = payload.get(key[:-4])
            if isinstance(value, bool) \
                    or not isinstance(value, (int, float)):
                return False
            if key.endswith("_max") and value > rule:
                return False
            if key.endswith("_min") and value < rule:
                return False
            continue
        if key not in payload:
            return False
        value = payload[key]
        if isinstance(rule, (list, tuple, set)):
            if value not in rule:
                return False
        elif value != rule:
            return False
    return True


async def require(gate, trust: TrustLedger, run_log, action: str,
                  channel: str, summary: str,
                  payload: dict[str, Any] | None = None,
                  requester: str = "agent") -> bool:
    """The trust-aware gate packages call instead of `gate.require`.

    - `auto` actions (owner-granted in config/autonomy.yaml) pass without a
      human decision, but are still logged and clearly marked `trust_auto`.
      If the grant carries a payload scope, only payloads inside the scope
      auto-run; anything outside (or unverifiable) falls through to the
      human gate below — the scope is what the proposal promised.
    - `gate` actions go through the normal approval gate; the decision —
      approved unchanged / approved after edit / denied — is recorded in
      the ledger, and a proposal event fires when confidence crosses the
      configured threshold.
    """
    if trust.level(action) == "auto":
        scope = trust.scope(action)
        if scope_allows(scope, payload):
            trust.record(action, "auto")
            trust.save()
            run_log.step("trust_auto", status="ok", action=action,
                         note="owner granted `auto` in config/autonomy.yaml — "
                              "executed without a human decision, fully logged")
            return True
        run_log.step("trust_scope", status="would", action=action,
                     note="payload outside the scope granted in "
                          "config/autonomy.yaml — routed to the approval gate")

    approved = await gate.require(action, channel, summary,
                                  payload=payload, requester=requester)
    decision = getattr(gate, "last_decision", None)
    if not approved:
        outcome = "denied"
    elif decision is not None and getattr(decision, "edited", False):
        outcome = "edited"
    else:
        outcome = "approved"
    trust.record(action, outcome)
    trust.save()

    prop = trust.check_proposal(action)
    if prop:
        trust.save()
        run_log.step("trust_proposal", status="would", action=action,
                     attempts=prop["attempts"],
                     approved_unchanged=prop["approved_unchanged"],
                     confidence=prop["confidence"], text=prop["text"])
        print(f"\n*** TRUST PROPOSAL ({action}) ***\n{prop['text']}\n")
        try:
            from .notify import SlackNotifier
            res = await SlackNotifier().send(
                f":handshake: Trust proposal — {prop['text']}")
            run_log.step("trust_proposal_notify", status=res.status)
        except Exception:
            pass
    return approved
