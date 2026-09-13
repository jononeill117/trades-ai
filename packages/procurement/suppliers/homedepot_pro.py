"""Home Depot Pro adapter — pro pricing often behind a Pro Xtra/login wall."""

from __future__ import annotations

from .base import SupplierAdapter


class HomeDepotProAdapter(SupplierAdapter):
    name = "homedepot_pro"
    base_url = "https://www.homedepot.com"
    search_path = "/s/{q}"
    card_re = r'<div class="product"[^>]*>.*?</div>'
    login_re = r"sign in .{0,30}price|pro price.+sign in"
