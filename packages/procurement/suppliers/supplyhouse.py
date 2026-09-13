"""SupplyHouse adapter — plumbing/HVAC supply with mostly-public pricing."""

from __future__ import annotations

from .base import SupplierAdapter


class SupplyHouseAdapter(SupplierAdapter):
    name = "supplyhouse"
    base_url = "https://www.supplyhouse.com"
    search_path = "/search?q={q}"
    card_re = r'<div class="product"[^>]*>.*?</div>'
