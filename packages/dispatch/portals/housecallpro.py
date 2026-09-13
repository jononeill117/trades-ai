"""Housecall Pro adapter — COMMUNITY EXAMPLE, untested against a live tenant.

Same deal as the ServiceTitan example: the flow is implemented, but the URLs
and selectors are yours to fill in under config/portals.housecallpro.yaml —
Housecall Pro's UI changes, and your account's screens are the authority.
Sign in once via a Solari browser profile rather than scripting credentials.
"""

from __future__ import annotations

from .base import SelectorAdapter


class HousecallProAdapter(SelectorAdapter):
    name = "housecallpro"
