"""Jobber adapter — COMMUNITY EXAMPLE, untested against a live tenant.

Implemented against the SelectorAdapter contract; wire your tenant's URLs and
selectors in config/portals.jobber.yaml. Use a Solari browser profile + login
handoff for auth — see docs/adapter-guide.md.
"""

from __future__ import annotations

from .base import SelectorAdapter


class JobberAdapter(SelectorAdapter):
    name = "jobber"
