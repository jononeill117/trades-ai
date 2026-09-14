"""Selector-health checks for procurement supplier adapters.

Mock mode: each enabled supplier's extraction patterns must still find a
product card and a price in its bundled fixture page. Live mode: additionally
fetches the supplier's real search URL (only registered when --live — real
sites may block plain HTTP fetches, which is itself a useful signal).
"""

from __future__ import annotations

import urllib.request

from core.config import repo_root

from .suppliers import load_suppliers


def checks(mode: str) -> list[dict]:
    out: list[dict] = []
    for supplier in load_suppliers():
        fixture = f"fixtures/supplier_pages/{supplier.name}.html"
        if (repo_root() / fixture).exists():
            out.append({
                "name": f"procurement/{supplier.name}:fixture",
                "html": fixture,
                "patterns": {
                    "product-card": supplier.card_re,
                    "price": supplier.price_re,
                },
            })
        if mode == "live":
            out.append({
                "name": f"procurement/{supplier.name}:live-search",
                "live": True,
                "fetch": _fetch(supplier.base_url + supplier.search_path.format(q="valve")),
                "patterns": {"page-not-empty": r"<html|<body|.{200}"},
            })
    return out


def _fetch(url: str):
    def fetch() -> str:
        req = urllib.request.Request(url, headers={"user-agent": "trades-ai-healthcheck/1.0"})
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.read().decode("utf-8", "replace")
    return fetch
