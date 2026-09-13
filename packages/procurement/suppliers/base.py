"""SupplierAdapter — price-check one supplier's web catalog.

Same pattern as portal adapters: the adapter drives a PageDriver (a Solari
cloud browser in live mode, a fixture-backed driver in mock mode) to the
part's search URL and extracts an Offer from the returned HTML.

Extraction is regex/config-driven on purpose — supplier markup changes often
enough that the patterns belong in config/suppliers.yaml where a deployer can
fix them without touching Python.

Login-gated pricing is handled gracefully: when a page shows "sign in for
pricing" the offer comes back with login_required=True and whatever public
list price was visible. To get real contract pricing, attach your own
logged-in Solari browser profile — see docs/adapter-guide.md. Credentials
never live in this repo.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models import Offer, Part


class SupplierAdapter:
    name = "supplier"
    base_url = ""
    search_path = "/search?q={q}"        # {q} = the part SKU or description
    # Regex defaults — override per-supplier in config/suppliers.yaml.
    card_re = r'<div class="product"[^>]*>.*?</div>'
    price_re = r"\$\s*([0-9][0-9,]*\.?[0-9]{0,2})"
    login_re = r"sign in|log ?in .{0,20}(price|pricing)|member price"
    oos_re = r"out of stock|unavailable|backorder"
    card_sku_attr = "data-sku"

    def __init__(self, base_url: str = "", patterns: dict[str, str] | None = None):
        if base_url:
            self.base_url = base_url.rstrip("/")
        for k, v in (patterns or {}).items():
            setattr(self, k, v)

    def search_url(self, part: Part) -> str:
        return self.base_url + self.search_path.format(q=part.sku)

    async def fetch_offer(self, driver, part: Part) -> Offer:
        url = self.search_url(part)
        await driver.goto(url)
        html = await driver.content()
        return self.extract(html, part, url)

    # -- extraction -----------------------------------------------------------

    def extract(self, html: str, part: Part, url: str = "") -> Offer:
        if re.search(self.login_re, html, re.I) and not self._price_in(html, part.sku):
            return Offer(
                supplier=self.name, part_sku=part.sku, unit_price=None, url=url,
                login_required=True, in_stock=True,
                notes="pricing behind login — attach a Solari profile with your session",
            )
        card = self._find_card(html, part.sku)
        if card is None:
            return Offer(supplier=self.name, part_sku=part.sku, unit_price=None,
                         url=url, notes="no matching product card found")
        price = self._price_in(card)
        oos = bool(re.search(self.oos_re, card, re.I))
        login = bool(re.search(self.login_re, card, re.I))
        return Offer(
            supplier=self.name, part_sku=part.sku, unit_price=price, url=url,
            in_stock=not oos, login_required=login and price is None,
            matched_desc=self._title_in(card) or part.description,
        )

    def _find_card(self, html: str, sku: str) -> Optional[str]:
        for card in re.findall(self.card_re, html, re.S | re.I):
            if sku.lower() in card.lower():
                return card
        return None

    def _price_in(self, html: str, sku: str = "") -> Optional[float]:
        scope = self._find_card(html, sku) if sku else html
        if scope is None:
            return None
        m = re.search(self.price_re, scope)
        return float(m.group(1).replace(",", "")) if m else None

    def _title_in(self, card: str) -> str:
        m = re.search(r'class="[^"]*(?:title|name)[^"]*"[^>]*>([^<]+)<', card, re.I)
        return m.group(1).strip() if m else ""
