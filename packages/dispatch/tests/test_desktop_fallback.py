"""Desktop fallback tests — the concurrency-limit skip path and the mock path.

On Solari free-plan accounts only one session can be open at a time, so the
desktop fallback must degrade gracefully when the portal sandbox already
holds the slot (instead of crashing the whole dispatch run).
"""

import asyncio
import json
from pathlib import Path

from core.audit import RunLog
from packages.dispatch import desktop_fallback, mock_portal_server
from packages.dispatch.desktop_fallback import _confirm_over_http

SEED = Path(__file__).parents[3] / "fixtures" / "mock_portal" / "seed.json"


class ConcurrencyLimitError(Exception):
    """Same name/shape as solari_core.errors.ConcurrencyLimitError."""


class _LimitedCore:
    """core whose desktop() fails like a free-plan Solari account."""

    def __init__(self):
        self.desktop_calls = 0

    async def desktop(self, **kwargs):
        self.desktop_calls += 1
        raise ConcurrencyLimitError("Too many concurrent sessions")


class _ExplodingCore:
    """core whose desktop() fails for a reason that is NOT the limit."""

    async def desktop(self, **kwargs):
        raise RuntimeError("something else broke")


def _run_log(tmp_path):
    return RunLog("dispatch", "mock", runs_dir=tmp_path)


def _log_text(run_log):
    return Path(run_log.path).read_text()


def test_skip_on_concurrency_limit(tmp_path):
    core = _LimitedCore()
    run_log = _run_log(tmp_path)
    status = asyncio.run(desktop_fallback.confirm_on_dispatch_board(
        core, run_log, "http://portal.example/jobs/JOB-1/board", "http://portal.example"))
    # The job was already booked via the browser — the fallback degrades to a
    # booked status instead of raising.
    assert status.startswith("booked")
    assert "concurrency" in status.lower()
    assert core.desktop_calls == 1
    # ...and the skip is in the audit trail.
    entry = [json.loads(line) for line in _log_text(run_log).splitlines()
             if '"desktop_fallback"' in line][0]
    assert entry["status"] == "skipped"


def test_non_limit_error_still_raises(tmp_path):
    core = _ExplodingCore()
    run_log = _run_log(tmp_path)
    try:
        asyncio.run(desktop_fallback.confirm_on_dispatch_board(
            core, run_log, "http://portal.example/jobs/JOB-1/board"))
    except RuntimeError as exc:
        assert "something else broke" in str(exc)
    else:
        raise AssertionError("expected the non-limit error to propagate")


def test_mock_desktop_path_confirms_over_http(tmp_path):
    # The mock-desktop branch (a desk with `.actions`) logs the computer-use
    # steps, then performs the real state change over HTTP against FieldDesk.
    server, base_url = mock_portal_server.serve(SEED)
    try:
        from core.drivers import HttpDriver
        from packages.dispatch.models import Slot, WorkOrder
        from packages.dispatch.portals.mock_portal import MockPortalAdapter

        async def go():
            adapter = MockPortalAdapter(base_url)
            driver = HttpDriver(base_url)
            assert await adapter.login(driver)
            order = WorkOrder(source_id="WO-DF", customer_name="Fallback Co",
                              site_address="9 Test Ave", trade="plumbing")
            slot = Slot(tech="Ray", start="2026-09-14 11:00", end="2026-09-14 13:00")
            job_ref = await adapter.create_job(driver, order, slot)

            class _FakeDesk:
                # presence of `.actions` selects the mock branch
                def __init__(self):
                    self.actions = []
                    from core.mock_solari import _MockMouse, _MockKeyboard
                    self.mouse = _MockMouse(self.actions)
                    self.keyboard = _MockKeyboard(self.actions)
                    self.sessionId = "mock_desk_df"

                async def open(self, app):
                    self.actions.append(f"open({app})")
                    return 4242

                async def screenshot(self, format="png"):
                    self.actions.append(f"screenshot({format})")
                    return b"PNG"

            class _FakeCore:
                def __init__(self):
                    self.desk = _FakeDesk()

                async def desktop(self, **kwargs):
                    return self.desk

                async def destroy_desktop(self, desk):
                    pass

            core = _FakeCore()
            run_log = _run_log(tmp_path)
            status = await desktop_fallback.confirm_on_dispatch_board(
                core, run_log, job_ref + "/board", base_url)
            assert status == "booked"
            assert core.desk.actions, "expected computer-use actions to be logged"

        asyncio.run(go())
    finally:
        server.shutdown()


def test_confirm_over_http_marks_booked():
    server, base_url = mock_portal_server.serve(SEED)
    try:
        from core.drivers import HttpDriver
        from packages.dispatch.models import Slot, WorkOrder
        from packages.dispatch.portals.mock_portal import MockPortalAdapter

        async def go():
            adapter = MockPortalAdapter(base_url)
            driver = HttpDriver(base_url)
            assert await adapter.login(driver)
            order = WorkOrder(source_id="WO-DF2", customer_name="Fallback Co",
                              site_address="9 Test Ave", trade="plumbing")
            slot = Slot(tech="Ray", start="2026-09-14 11:00", end="2026-09-14 13:00")
            job_ref = await adapter.create_job(driver, order, slot)
            assert await _confirm_over_http(job_ref + "/board", base_url) == "booked"

        asyncio.run(go())
    finally:
        server.shutdown()
