"""MockPortal — the fully working adapter for the bundled FieldDesk portal.

This is the reference implementation: it really books jobs, against a portal
that really exists (a tiny stdlib web app shipped in this repo). In --mock
mode the portal runs on localhost and the driver is HttpDriver; in --live
mode the same portal runs inside a Solari sandbox behind a preview URL and
the driver is a Solari cloud browser.
"""

from __future__ import annotations

from ..models import NeedsDesktopError, Slot, WorkOrder


class MockPortalAdapter:
    name = "mock"
    # Selectors are configurable — same pattern the community adapters use.
    DEFAULT_SELECTORS = {
        "login.email": "#email",
        "login.password": "#password",
        "login.submit": "#login-submit",
        "job.customer": "#customer_name",
        "job.site": "#site_address",
        "job.tenant": "#tenant_name",
        "job.tenant_phone": "#tenant_phone",
        "job.trade": "#trade",
        "job.priority": "#priority",
        "job.window_start": "#window_start",
        "job.window_end": "#window_end",
        "job.notes": "#notes",
        "job.submit": "#create-job",
        "confirm.button": "#confirm-booking",
        "confirm.status": "#job-status",
        "job.id": "#job-id",
    }

    def __init__(self, base_url: str, selectors: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.sel = {**self.DEFAULT_SELECTORS, **(selectors or {})}

    async def login(self, driver) -> bool:
        await driver.goto(self.base_url + "/login")
        await driver.fill(self.sel["login.email"], "dispatcher@yourshop.example")
        await driver.fill(self.sel["login.password"], "demo-password")
        await driver.click(self.sel["login.submit"])
        return "/login" not in driver.current_url

    async def create_job(self, driver, order: WorkOrder, slot: Slot) -> str:
        s = self.sel
        await driver.goto(self.base_url + "/jobs/new")
        await driver.fill(s["job.customer"], order.customer_name)
        await driver.fill(s["job.site"], order.site_address)
        await driver.fill(s["job.tenant"], order.tenant_name)
        await driver.fill(s["job.tenant_phone"], order.tenant_phone)
        await driver.fill(s["job.trade"], order.trade)
        await driver.fill(s["job.priority"], order.priority)
        await driver.fill(s["job.window_start"], slot.start)
        await driver.fill(s["job.window_end"], slot.end)
        await driver.fill(s["job.notes"], order.notes)
        await driver.click(s["job.submit"])  # portal redirects to /jobs/<id>
        return driver.current_url

    async def confirm_booking(self, driver, job_ref: str) -> str:
        await driver.goto(job_ref)
        await driver.click(self.sel["confirm.button"])
        status = await driver.text(self.sel["confirm.status"])
        if status == "pending_board":
            # The portal insists the final confirm happens on the dispatch
            # board — a GUI-only step. Hand off to a Solari desktop.
            raise NeedsDesktopError(f"{job_ref} requires dispatch-board confirmation")
        return status
