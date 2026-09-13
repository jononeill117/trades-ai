"""Ferguson adapter — wholesale plumbing/HVAC supplier.

Contract pricing is typically login-gated; public pages show list price. With
no profile attached you'll get list price or a login_required flag. Attach a
Solari browser profile you've signed into (see docs/adapter-guide.md) for
your real pricing.
"""

from __future__ import annotations

from .base import SupplierAdapter


class FergusonAdapter(SupplierAdapter):
    name = "ferguson"
    base_url = "https://www.ferguson.com"
    search_path = "/searchResult?q={q}"
    card_re = r'<div class="product"[^>]*>.*?</div>'
    login_re = r"sign in .{0,30}price|your price.+sign in"
