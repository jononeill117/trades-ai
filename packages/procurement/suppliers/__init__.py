"""Supplier adapters registry. Add yours in config/suppliers.yaml + a class
here — see docs/adapter-guide.md."""

from __future__ import annotations

from core.config import load_yaml, repo_root

from .base import SupplierAdapter
from .ferguson import FergusonAdapter
from .homedepot_pro import HomeDepotProAdapter
from .supplyhouse import SupplyHouseAdapter

REGISTRY = {
    "ferguson": FergusonAdapter,
    "supplyhouse": SupplyHouseAdapter,
    "homedepot_pro": HomeDepotProAdapter,
}


def load_suppliers(cfg: dict | None = None) -> list[SupplierAdapter]:
    """Instantiate configured suppliers (defaults: all three)."""
    cfg = cfg or load_yaml(repo_root() / "config" / "suppliers.yaml")
    out: list[SupplierAdapter] = []
    for name, cls in REGISTRY.items():
        section = cfg.get("suppliers", {}).get(name, {})
        if section.get("enabled", True):
            out.append(cls(base_url=section.get("base_url", ""),
                           patterns=section.get("patterns")))
    return out


def supplier_config(name: str, cfg: dict | None = None) -> dict:
    cfg = cfg or load_yaml(repo_root() / "config" / "suppliers.yaml")
    return cfg.get("suppliers", {}).get(name, {})
