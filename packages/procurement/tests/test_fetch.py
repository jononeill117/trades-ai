"""Fetch tests — live-mode sequencing and the SOLARI_STEALTH opt-in.

Live mode runs suppliers sequentially (Solari free-plan accounts allow one
concurrent session); stealth + proxy + captcha are opt-in via SOLARI_STEALTH=1
because Solari returns HTTP 402 for stealth on the free plan.
"""

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from core.audit import RunLog
from core.drivers import FixtureDriver
from packages.procurement.fetch import _fetch_supplier_live, fetch_all
from packages.procurement.parts_list import load_parts
from packages.procurement.suppliers import load_suppliers

FIXTURES = Path(__file__).parents[3] / "fixtures"


def _pages_for(supplier):
    path = FIXTURES / "supplier_pages" / f"{supplier.name}.html"
    host = urlparse(supplier.base_url).netloc or supplier.name
    html = path.read_text()
    return {host: html, supplier.name: html}


class _FakeSession:
    def __init__(self, rec, tag):
        self.id = f"fake_browser_{tag}"
        self._rec = rec

    async def close(self):
        self._rec["open"] -= 1


class _FakeCore:
    """Records browser() kwargs and the peak number of open sessions."""

    def __init__(self, suppliers):
        self._rec = {"calls": [], "open": 0, "max_open": 0}
        self._pages = {}
        for s in suppliers:
            self._pages.update(_pages_for(s))

    @property
    def rec(self):
        return self._rec

    async def browser(self, **kwargs):
        self._rec["calls"].append(kwargs)
        self._rec["open"] += 1
        self._rec["max_open"] = max(self._rec["max_open"], self._rec["open"])
        # Yield so a parallel implementation would interleave here.
        await asyncio.sleep(0.01)
        return _FakeSession(self._rec, len(self._rec["calls"])), FixtureDriver(self._pages)

    async def replay_url(self, session_id):
        return None


def _run_log(tmp_path):
    return RunLog("procurement", "mock", runs_dir=tmp_path)


def test_live_fetch_is_sequential(tmp_path):
    suppliers = load_suppliers({"suppliers": {}})
    parts = load_parts(FIXTURES / "parts_list.csv")
    core = _FakeCore(suppliers)
    offers = asyncio.run(fetch_all(
        core, suppliers, parts, _run_log(tmp_path), mode="live",
        fixtures_dir=FIXTURES / "supplier_pages"))
    assert len(core.rec["calls"]) == len(suppliers)
    # Never more than one browser open at a time.
    assert core.rec["max_open"] == 1
    assert core.rec["open"] == 0  # all sessions closed
    # Offers still come back for every supplier x part.
    assert len(offers) == len(suppliers) * len(parts)


def test_mock_fetch_stays_parallel(tmp_path):
    # Mock mode keeps the asyncio.gather path — just verify it still works.
    suppliers = load_suppliers({"suppliers": {}})
    parts = load_parts(FIXTURES / "parts_list.csv")
    core = _FakeCore(suppliers)
    offers = asyncio.run(fetch_all(
        core, suppliers, parts, _run_log(tmp_path), mode="mock",
        fixtures_dir=FIXTURES / "supplier_pages"))
    assert len(offers) == len(suppliers) * len(parts)


def test_stealth_opt_in(tmp_path, monkeypatch):
    suppliers = load_suppliers({"suppliers": {}})
    supplier = next(s for s in suppliers if s.name == "ferguson")
    parts = load_parts(FIXTURES / "parts_list.csv")[:1]

    monkeypatch.setenv("SOLARI_STEALTH", "1")
    core = _FakeCore([supplier])
    asyncio.run(_fetch_supplier_live(core, supplier, parts, _run_log(tmp_path), None))
    kwargs = core.rec["calls"][0]
    assert kwargs["stealth"] is True
    assert kwargs["proxy"] is not None
    assert kwargs["captcha"] is True

    monkeypatch.delenv("SOLARI_STEALTH", raising=False)
    core = _FakeCore([supplier])
    asyncio.run(_fetch_supplier_live(core, supplier, parts, _run_log(tmp_path), None))
    kwargs = core.rec["calls"][0]
    assert kwargs["stealth"] is False
    assert kwargs["proxy"] is None
    assert kwargs["captcha"] is False


def test_supplier_failure_yields_error_offers(tmp_path):
    # A supplier with no fixture page raises inside FixtureDriver.goto;
    # fetch_all must degrade to error offers, not crash the run.
    suppliers = load_suppliers({"suppliers": {}})
    supplier = next(s for s in suppliers if s.name == "ferguson")
    parts = load_parts(FIXTURES / "parts_list.csv")
    core = _FakeCore([])  # no pages -> goto raises LookupError
    offers = asyncio.run(fetch_all(
        core, [supplier], parts, _run_log(tmp_path), mode="live",
        fixtures_dir=FIXTURES / "supplier_pages"))
    assert len(offers) == len(parts)
    assert all(o.unit_price is None for o in offers)
    assert all("fetch failed" in (o.notes or "") for o in offers)
