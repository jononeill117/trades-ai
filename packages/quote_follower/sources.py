"""Quote sources — adapter contract for where the quote list comes from.

- `CsvQuoteSource`: the baseline. Any FSM/CRM can export a CSV; this source
  always works.
- `BrowserQuoteSource`: for systems without an API/export — drives the web
  UI's quote list through a PageDriver (Solari cloud browser live, fixture
  page in mock) and scrapes the table with config selectors.
- API sources: implement `fetch_quotes() -> list[Quote]` against your system's
  API — the contract is the same.

The pipeline doesn't care which produced the list; sources return Quote rows
and the classification/follow-up logic is identical.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Protocol

from .models import Quote

DEFAULT_SELECTORS = {
    "row": r'<tr class="quote-row"[^>]*>(.*?)</tr>',
    "cell": r'<td class="q-([a-z_]+)"[^>]*>([^<]*)</td>',
}


class QuoteSource(Protocol):
    name: str
    async def fetch_quotes(self) -> list[Quote]: ...


class CsvQuoteSource:
    name = "csv"

    def __init__(self, path: Path):
        self.path = path

    async def fetch_quotes(self) -> list[Quote]:
        rows = csv.DictReader(self.path.read_text().splitlines())
        return [Quote.from_row(r) for r in rows
                if any((v or "").strip() for v in r.values())]


class BrowserQuoteSource:
    """Scrapes the quotes list page of a web-only FSM/CRM."""
    name = "browser"

    def __init__(self, driver, list_url: str, selectors: dict | None = None):
        self.driver = driver
        self.list_url = list_url
        self.sel = {**DEFAULT_SELECTORS, **(selectors or {})}

    async def fetch_quotes(self) -> list[Quote]:
        await self.driver.goto(self.list_url)
        html = await self.driver.content()
        quotes = []
        for row_html in re.findall(self.sel["row"], html, re.S):
            cells = {k: v.strip() for k, v in
                     re.findall(self.sel["cell"], row_html)}
            quotes.append(Quote.from_row({
                "quote_id": cells.get("id", ""),
                "customer": cells.get("customer", ""),
                "amount": cells.get("amount", ""),
                "status": cells.get("status", ""),
            }))
        return quotes
