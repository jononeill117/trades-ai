"""End-to-end: book a job in the bundled FieldDesk portal via HttpDriver."""

import asyncio
from pathlib import Path

from core.drivers import HttpDriver
from packages.dispatch import mock_portal_server
from packages.dispatch.desktop_fallback import _confirm_over_http
from packages.dispatch.models import NeedsDesktopError, Slot, WorkOrder
from packages.dispatch.portals.mock_portal import MockPortalAdapter

SEED = Path(__file__).parents[3] / "fixtures" / "mock_portal" / "seed.json"


def _book():
    server, base_url = mock_portal_server.serve(SEED)
    try:
        async def go():
            adapter = MockPortalAdapter(base_url)
            driver = HttpDriver(base_url)
            assert await adapter.login(driver)
            order = WorkOrder(source_id="WO-T1", customer_name="Test Co",
                              site_address="1 Main St", trade="plumbing")
            slot = Slot(tech="Ray", start="2026-09-14 11:00", end="2026-09-14 13:00")
            job_ref = await adapter.create_job(driver, order, slot)
            assert "/jobs/" in job_ref
            # gui_confirm_required in seed -> browser confirm raises NeedsDesktop
            try:
                await adapter.confirm_booking(driver, job_ref)
                raise AssertionError("expected NeedsDesktopError")
            except NeedsDesktopError:
                pass
            # the fallback's HTTP path really confirms it
            status = await _confirm_over_http(job_ref + "/board", base_url)
            assert status == "booked"
            return job_ref

        return asyncio.run(go())
    finally:
        server.shutdown()


def test_booking_round_trip():
    job_ref = _book()
    assert "/jobs/JOB-" in job_ref
