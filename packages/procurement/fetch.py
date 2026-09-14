"""Price-check — one Solari cloud browser per supplier.

Live mode runs suppliers sequentially (Solari free-plan accounts allow one
concurrent session, so parallelism is a mock-mode optimization only).
Stealth + managed residential proxy + captcha solving are opt-in via
SOLARI_STEALTH=1 — they need a paid Solari plan. Mock mode: a FixtureDriver
serves the cached supplier pages under fixtures/supplier_pages/ — the same
extraction code runs either way.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from urllib.parse import urlparse

from core.config import env
from core.drivers import FixtureDriver, as_driver

from .models import Offer, Part
from .suppliers.base import SupplierAdapter


def _fixture_pages(fixtures_dir: Path, supplier: SupplierAdapter) -> dict[str, str]:
    """Cached pages keyed so the supplier's real URLs resolve to them —
    FixtureDriver matches 'key in url', so the key is the supplier's host."""
    path = fixtures_dir / f"{supplier.name}.html"
    if not path.exists():
        return {}
    host = urlparse(supplier.base_url).netloc or supplier.name
    return {host: path.read_text(), supplier.name: path.read_text()}


async def _fetch_supplier_mock(supplier: SupplierAdapter, parts: list[Part],
                               fixtures_dir: Path) -> list[Offer]:
    driver = FixtureDriver(_fixture_pages(fixtures_dir, supplier))
    return [await supplier.fetch_offer(driver, p) for p in parts]


async def _fetch_supplier_live(core, supplier: SupplierAdapter, parts: list[Part],
                               run_log, profile: str | None) -> list[Offer]:
    # Stealth + residential proxy + captcha solving require a paid Solari
    # plan (402 on free). Default to plain browsers; set SOLARI_STEALTH=1
    # to opt into the full stealth stack on a paid plan.
    stealth = env("SOLARI_STEALTH", "") == "1"
    session, page = await core.browser(
        profile_name=profile, recording=True, stealth=stealth,
        proxy="us" if stealth else None, captcha=stealth,
    )
    run_log.session("browser", session.id)
    t0 = time.monotonic()
    try:
        driver = as_driver(page)
        return [await supplier.fetch_offer(driver, p) for p in parts]
    finally:
        await session.close()
        run_log.usage("browser", session.id, time.monotonic() - t0)
        run_log.session("browser", session.id, replay_url=await core.replay_url(session.id))


async def fetch_all(core, suppliers: list[SupplierAdapter], parts: list[Part],
                    run_log, *, mode: str, fixtures_dir: Path,
                    profiles: dict[str, str] | None = None) -> list[Offer]:
    """All suppliers in parallel; parts sequential within each (politeness).

    Live mode runs suppliers sequentially: Solari accounts with a low
    session concurrency limit cannot hold multiple cloud browsers open at
    once, so parallelism is a mock-mode optimization only.
    """
    profiles = profiles or {}

    async def one(supplier: SupplierAdapter) -> list[Offer]:
        try:
            if mode == "live":
                return await _fetch_supplier_live(
                    core, supplier, parts, run_log, profiles.get(supplier.name))
            return await _fetch_supplier_mock(supplier, parts, fixtures_dir)
        except Exception as exc:
            run_log.step("price_check", status="error",
                         supplier=supplier.name, error=str(exc)[:200])
            return [Offer(supplier=supplier.name, part_sku=p.sku, unit_price=None,
                          notes=f"fetch failed: {exc}") for p in parts]

    if mode == "live":
        nested = [await one(s) for s in suppliers]
    else:
        nested = await asyncio.gather(*(one(s) for s in suppliers))
    return [o for offers in nested for o in offers]
