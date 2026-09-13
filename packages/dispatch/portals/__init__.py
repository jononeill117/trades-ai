"""Portal adapters registry.

`mock` is fully working (bundled FieldDesk portal). The rest are community
examples driven by selector config — see docs/adapter-guide.md.
"""

from __future__ import annotations

from pathlib import Path

from core.config import load_yaml, repo_root
from .base import PortalAdapter, SelectorAdapter
from .mock_portal import MockPortalAdapter
from .servicetitan import ServiceTitanAdapter
from .housecallpro import HousecallProAdapter
from .jobber import JobberAdapter

COMMUNITY = {
    "servicetitan": ServiceTitanAdapter,
    "housecallpro": HousecallProAdapter,
    "jobber": JobberAdapter,
}


def get_adapter(name: str, base_url: str = "") -> PortalAdapter:
    """Build a portal adapter by name. Community adapters read their selector
    map from config/portals.<name>.yaml."""
    if name == "mock":
        return MockPortalAdapter(base_url)
    cls = COMMUNITY.get(name)
    if cls is None:
        raise KeyError(f"unknown portal adapter {name!r} (have: mock, {', '.join(COMMUNITY)})")
    cfg_path = repo_root() / "config" / f"portals.{name}.yaml"
    cfg = load_yaml(cfg_path) if cfg_path.exists() else {}
    return cls(base_url or cfg.get("base_url", ""), cfg.get("selectors", {}))
