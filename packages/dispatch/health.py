"""Selector-health checks for the dispatch adapters.

Mock mode: boots the bundled FieldDesk portal locally and verifies every
selector in MockPortalAdapter.DEFAULT_SELECTORS resolves on the page the
adapter actually visits — including a freshly created job page (confirm
selectors only exist on non-booked jobs).

Live mode: for each community adapter with a configured base_url, fetches its
login page and checks the login selectors. Unconfigured -> not registered.
"""

from __future__ import annotations

import urllib.request
from urllib.parse import urlencode

from core.config import load_yaml, repo_root


def _portal_pages() -> dict[str, str]:
    """Serve the mock portal and return {page_name: html} the adapter uses."""
    from . import mock_portal_server

    server, base = mock_portal_server.serve(
        repo_root() / "fixtures" / "mock_portal" / "seed.json")
    try:
        def get(path: str, cookie: str = "session=ok") -> str:
            req = urllib.request.Request(base + path,
                                         headers={"cookie": cookie})
            with urllib.request.urlopen(req) as res:
                return res.read().decode()

        pages = {
            "login": get("/login", cookie=""),
            "new_job": get("/jobs/new"),
        }
        # A booked seed job has no confirm button — create a fresh job so the
        # confirm selectors can be checked on its page.
        req = urllib.request.Request(
            base + "/jobs", data=urlencode({
                "customer_name": "Healthcheck Co", "site_address": "1 Test Way",
                "tenant_name": "Check", "tenant_phone": "555-0100",
                "trade": "plumbing", "priority": "normal",
                "window_start": "2026-01-01 09:00", "window_end": "2026-01-01 11:00",
                "notes": "selector healthcheck"}).encode(),
            headers={"cookie": "session=ok"})
        res = urllib.request.urlopen(req)  # follows the 303 to the new job page
        job_path = res.geturl().rsplit(base, 1)[-1]
        res.read()
        pages["job_page"] = get(job_path)
        pages["board_page"] = get(job_path + "/board")
        return pages
    finally:
        server.shutdown()


def checks(mode: str) -> list[dict]:
    out: list[dict] = []
    try:
        pages = _portal_pages()
    except Exception as exc:
        return [{"name": "dispatch/mock-portal", "fetch": _boom(exc),
                 "selectors": ["#email"]}]

    out.append({"name": "dispatch/mock-portal:login", "html_text": pages["login"],
                "selectors": ["#email", "#password", "#login-submit"]})
    out.append({"name": "dispatch/mock-portal:new-job", "html_text": pages["new_job"],
                "selectors": ["#customer_name", "#site_address", "#tenant_name",
                              "#tenant_phone", "#trade", "#priority",
                              "#window_start", "#window_end", "#notes", "#create-job"]})
    out.append({"name": "dispatch/mock-portal:job", "html_text": pages["job_page"],
                "selectors": ["#job-id", "#job-status", "#confirm-booking"]})
    out.append({"name": "dispatch/mock-portal:board", "html_text": pages["board_page"],
                "selectors": ["#board-confirm"]})

    # Community adapters: only register when configured — live checks need a
    # real base_url, and we only verify the login page is reachable.
    if mode == "live":
        for name in ("servicetitan", "housecallpro", "jobber"):
            cfg = load_yaml(repo_root() / "config" / f"portals.{name}.yaml")
            base = (cfg.get("base_url") or "").rstrip("/")
            if not base:
                continue
            login_path = (cfg.get("selectors") or {}).get("url.login", "/login")
            login_sels = [v for k, v in (cfg.get("selectors") or {}).items()
                          if k.startswith("login.")]
            out.append({
                "name": f"dispatch/{name}:login",
                "live": True,
                "fetch": _fetch(base + login_path),
                "selectors": [s for s in login_sels if s.startswith("#")],
            })
    return out


def _boom(exc: Exception):
    def fetch() -> str:
        raise exc
    return fetch


def _fetch(url: str):
    def fetch() -> str:
        with urllib.request.urlopen(url, timeout=15) as res:
            return res.read().decode("utf-8", "replace")
    return fetch
