"""PortalAdapter — the interface between a booking and a field-service portal.

A portal adapter knows how to do three things in a portal's web UI:

    login(driver)                    -> True when signed in
    create_job(driver, order, slot)  -> portal job reference/URL
    confirm_booking(driver, ref)     -> final status string

`driver` is a core.drivers.PageDriver — a Solari cloud browser page in live
mode, or the stdlib HttpDriver in mock mode. Adapters raise NeedsDesktopError
when a step genuinely can't be done in a browser and must fall back to a
Solari desktop.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..models import NeedsDesktopError, Slot, WorkOrder


class PortalAdapter(Protocol):
    name: str
    base_url: str

    async def login(self, driver) -> bool: ...
    async def create_job(self, driver, order: WorkOrder, slot: Slot) -> str: ...
    async def confirm_booking(self, driver, job_ref: str) -> str: ...


class SelectorAdapter:
    """Base for config-driven adapters: every step is 'goto this URL, fill
    these selectors, click this selector'. The selector map lives in YAML so
    a deployer can adapt to their portal without touching Python."""

    name = "selector-adapter"

    def __init__(self, base_url: str, selectors: dict[str, str]):
        if not base_url:
            raise RuntimeError(
                f"{self.name}: no base_url configured — set it in "
                f"config/portals.{self.name}.yaml"
            )
        self.base_url = base_url.rstrip("/")
        self.sel = selectors

    def url(self, key: str, **fmt: Any) -> str:
        path = self.sel.get(f"url.{key}", "")
        return self.base_url + path.format(**fmt)

    async def login(self, driver) -> bool:
        s = self.sel
        await driver.goto(self.url("login"))
        if s.get("login.email"):
            await driver.fill(s["login.email"], self.sel.get("credentials.email", ""))
            await driver.fill(s["login.password"], self.sel.get("credentials.password", ""))
            await driver.click(s["login.submit"])
        return True

    async def create_job(self, driver, order: WorkOrder, slot: Slot) -> str:
        s = self.sel
        await driver.goto(self.url("new_job"))
        await driver.fill(s["job.customer"], order.customer_name)
        await driver.fill(s["job.site"], order.site_address)
        if s.get("job.tenant"):
            await driver.fill(s["job.tenant"], order.tenant_name)
        if s.get("job.tenant_phone"):
            await driver.fill(s["job.tenant_phone"], order.tenant_phone)
        if s.get("job.trade"):
            await driver.fill(s["job.trade"], order.trade)
        if s.get("job.priority"):
            await driver.fill(s["job.priority"], order.priority)
        await driver.fill(s["job.window_start"], slot.start)
        await driver.fill(s["job.window_end"], slot.end)
        if s.get("job.notes"):
            await driver.fill(s["job.notes"], order.notes)
        await driver.click(s["job.submit"])
        return driver.current_url

    async def confirm_booking(self, driver, job_ref: str) -> str:
        s = self.sel
        if job_ref.startswith("http"):
            await driver.goto(job_ref)
        else:
            await driver.goto(self.url("job", id=job_ref))
        if s.get("confirm.button"):
            await driver.click(s["confirm.button"])
        return await driver.text(s["confirm.status"])
