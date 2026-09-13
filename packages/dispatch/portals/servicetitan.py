"""ServiceTitan adapter — COMMUNITY EXAMPLE, untested against a live tenant.

ServiceTitan is a big, dynamic app; its real screens differ per tenant and
release. This adapter follows the SelectorAdapter contract: fill in
config/portals.servicetitan.yaml with the URLs and CSS selectors that match
YOUR tenant, and it drives them through the same PageDriver interface as the
working mock adapter.

Recommended live setup: create a Solari browser profile once, sign in by hand
(login handoff — see docs/adapter-guide.md), and every run starts already
authenticated. Do NOT put credentials in the selector config.
"""

from __future__ import annotations

from .base import SelectorAdapter


class ServiceTitanAdapter(SelectorAdapter):
    name = "servicetitan"
