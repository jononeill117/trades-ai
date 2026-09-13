"""Aggregation tests — the pure pricing math plus fixture-page extraction."""

from pathlib import Path

from packages.procurement.aggregate import aggregate_locally
from packages.procurement.models import Offer, Part
from packages.procurement.suppliers import load_suppliers
from packages.procurement.parts_list import load_parts

FIXTURES = Path(__file__).parents[3] / "fixtures"


def _extract_offers():
    """Run the real adapters' extraction over the cached supplier pages."""
    parts = load_parts(FIXTURES / "parts_list.csv")
    offers = []
    for supplier in load_suppliers({"suppliers": {}}):
        html = (FIXTURES / "supplier_pages" / f"{supplier.name}.html").read_text()
        offers += [supplier.extract(html, p, url=f"{supplier.base_url}/x") for p in parts]
    return parts, offers


def test_extraction_finds_prices_and_login_gate():
    parts, offers = _extract_offers()
    by = {(o.supplier, o.part_sku): o for o in offers}
    assert by[("ferguson", "WH-50G-NG")].unit_price == 649.00
    assert by[("supplyhouse", "WH-50G-NG")].in_stock is False
    assert by[("ferguson", "PTRAP-125")].login_required is True
    assert by[("homedepot_pro", "FLAP-2IN")].unit_price == 4.50


def test_picks_cheapest_compliant_per_line():
    parts, offers = _extract_offers()
    quote = aggregate_locally(parts, offers, "ferguson")
    winners = {l["part"]["sku"]: l["winner"] for l in quote["lines"]}
    # supplyhouse is OOS on the water heater -> homedepot wins at 598
    assert winners["WH-50G-NG"]["supplier"] == "homedepot_pro"
    assert winners["WH-50G-NG"]["unit_price"] == 598.00
    assert winners["FLAP-2IN"]["supplier"] == "supplyhouse"
    # ferguson's P-trap price is login-gated -> can't win even if cheaper
    assert winners["PTRAP-125"]["supplier"] == "supplyhouse"


def test_totals_and_savings():
    parts, offers = _extract_offers()
    quote = aggregate_locally(parts, offers, "ferguson")
    # winners: 598 + 6*3.25 + 4*6.75 = 598 + 19.50 + 27.00 = 644.50
    assert quote["total"] == 644.50
    # baseline (ferguson): 649 + 6*4.10 + (gated -> fallback max 7.20*4)
    # = 649 + 24.60 + 28.80 = 702.40
    assert quote["baseline_total"] == 702.40
    assert quote["savings"] == 57.90


def test_no_compliant_offer_flags_line():
    parts = [Part(sku="NOPE-1", description="nothing", qty=1)]
    offers = [Offer(supplier="x", part_sku="NOPE-1", unit_price=None,
                    login_required=True)]
    quote = aggregate_locally(parts, offers, "x")
    assert quote["lines"][0]["winner"] is None
    assert "login-gated" in quote["lines"][0]["note"]
