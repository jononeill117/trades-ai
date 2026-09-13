"""FieldDesk — a fictional field-service portal, in one stdlib file.

It exists so the dispatch demo runs end to end with NO real credentials:
the MockPortal adapter books real jobs against it, and because it's a single
dependency-free Python file it can also run INSIDE a Solari sandbox — the
live demo drives a real Solari cloud browser against its public preview URL.

Run it yourself:   python mock_portal_server.py --port 8080
"""

from __future__ import annotations

import json
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STATE: dict = {"jobs": {}, "settings": {"gui_confirm_required": True}, "counter": 1000}

LOGIN_PAGE = """<!doctype html><title>FieldDesk — sign in</title>
<h1>FieldDesk</h1><p>Field-service portal (demo)</p>
<form method="post" action="/login">
<input name="email" id="email" placeholder="dispatcher@yourshop.example">
<input name="password" id="password" type="password">
<button type="submit" id="login-submit">Sign in</button></form>"""

NEW_JOB_PAGE = """<!doctype html><title>FieldDesk — new job</title>
<h1>New job</h1>
<form method="post" action="/jobs">
<label>Customer <input name="customer_name" id="customer_name"></label>
<label>Site <input name="site_address" id="site_address"></label>
<label>Tenant <input name="tenant_name" id="tenant_name"></label>
<label>Tenant phone <input name="tenant_phone" id="tenant_phone"></label>
<label>Trade <input name="trade" id="trade"></label>
<label>Priority <input name="priority" id="priority"></label>
<label>Window start <input name="window_start" id="window_start"></label>
<label>Window end <input name="window_end" id="window_end"></label>
<label>Notes <textarea name="notes" id="notes"></textarea></label>
<button type="submit" id="create-job">Create job</button></form>"""


def _page(title: str, inner: str) -> bytes:
    return f"<!doctype html><title>FieldDesk — {title}</title><h1>{title}</h1>{inner}".encode()


def _dashboard() -> bytes:
    rows = "".join(
        f'<li><a href="/jobs/{jid}">{jid}</a> — {j["customer_name"]} — '
        f'<span id="status-{jid}">{j["status"]}</span></li>'
        for jid, j in STATE["jobs"].items()
    )
    return _page("Jobs", f'<a href="/jobs/new" id="new-job-link">New job</a><ul>{rows}</ul>')


def _job_page(job: dict) -> bytes:
    confirm = ""
    if job["status"] != "booked":
        confirm = f"""<form method="post" action="/jobs/{job['id']}/confirm">
        <button type="submit" id="confirm-booking">Confirm booking</button></form>
        <p id="board-note">Final confirmation happens on the dispatch board.</p>"""
    return _page(
        f"Job {job['id']}",
        f'<p>ID: <span id="job-id">{job["id"]}</span></p>'
        f'<p>Status: <span id="job-status">{job["status"]}</span></p>'
        f'<p>Customer: <span id="job-customer">{job["customer_name"]}</span></p>'
        f'<p>Site: <span id="job-site">{job["site_address"]}</span></p>'
        f'<p>Window: <span id="job-window">{job["window_start"]} to {job["window_end"]}</span></p>'
        + confirm,
    )


def _board_page(job: dict) -> bytes:
    """The 'dispatch board' — presented as a GUI-app screen. The desktop
    fallback opens this in the desktop's Chrome and clicks CONFIRM."""
    return _page(
        "Dispatch board",
        f'<p id="board-job">{job["id"]} — {job["customer_name"]} @ {job["site_address"]}</p>'
        f'<form method="post" action="/jobs/{job["id"]}/confirm">'
        f'<input type="hidden" name="via" value="board">'
        f'<button type="submit" id="board-confirm" autofocus style="font-size:2em">CONFIRM</button></form>',
    )


def _next_job_id() -> str:
    STATE["counter"] += 1
    return f"JOB-{STATE['counter']}"


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, status: int = 200, headers: dict | None = None):
        self.send_response(status)
        self.send_header("content-type", "text/html; charset=utf-8")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to: str, headers: dict | None = None):
        self._send(b"", status=303, headers={"location": to, **(headers or {})})

    def _signed_in(self) -> bool:
        return "session=ok" in (self.headers.get("cookie") or "")

    def _form(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        data = parse_qs(self.rfile.read(length).decode())
        return {k: v[0] for k, v in data.items()}

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/login":
            return self._send(LOGIN_PAGE.encode())
        if not self._signed_in():
            return self._redirect("/login")
        if path == "/":
            return self._send(_dashboard())
        if path == "/jobs/new":
            return self._send(NEW_JOB_PAGE.encode())
        if path.startswith("/jobs/") and path.endswith("/board"):
            job = STATE["jobs"].get(path.split("/")[2])
            return self._send(_board_page(job) if job else _page("404", "no job"), 200 if job else 404)
        if path.startswith("/jobs/"):
            job = STATE["jobs"].get(path.split("/")[2])
            return self._send(_job_page(job) if job else _page("404", "no job"), 200 if job else 404)
        return self._send(_page("404", "not found"), 404)

    def do_POST(self):
        path = urlparse(self.path).path
        form = self._form()
        if path == "/login":
            return self._redirect("/", {"set-cookie": "session=ok; Path=/"})
        if not self._signed_in():
            return self._redirect("/login")
        if path == "/jobs":
            jid = _next_job_id()
            STATE["jobs"][jid] = {
                "id": jid, "status": "scheduled",
                **{k: form.get(k, "") for k in (
                    "customer_name", "site_address", "tenant_name", "tenant_phone",
                    "trade", "priority", "window_start", "window_end", "notes")},
            }
            return self._redirect(f"/jobs/{jid}")
        if path.startswith("/jobs/") and path.endswith("/confirm"):
            job = STATE["jobs"].get(path.split("/")[2])
            if not job:
                return self._send(_page("404", "no job"), 404)
            if STATE["settings"].get("gui_confirm_required") and form.get("via") != "board":
                job["status"] = "pending_board"
                return self._send(_job_page(job))
            job["status"] = "booked"
            return self._send(_job_page(job))
        return self._send(_page("404", "not found"), 404)


def load_seed(seed_path: Path | str | None) -> None:
    """Pre-load jobs/settings from fixtures/mock_portal/seed.json."""
    if not seed_path:
        return
    data = json.loads(Path(seed_path).read_text())
    STATE["jobs"].update({j["id"]: j for j in data.get("jobs", [])})
    STATE["settings"].update(data.get("settings", {}))
    if STATE["jobs"]:
        STATE["counter"] = max(int(j.split("-")[1]) for j in STATE["jobs"])


def serve(seed_path: Path | str | None = None, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the portal on an ephemeral local port. Returns (server, base_url)."""
    load_seed(seed_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


async def serve_in_sandbox(sbx, seed_path: Path | str | None = None, port: int = 8321) -> str:
    """Run the portal INSIDE a Solari sandbox and return its public preview
    URL — this is how a Solari cloud browser reaches it in --live mode."""
    await sbx.files.write("/tmp/portal_server.py", Path(__file__).read_text())
    if seed_path:
        await sbx.files.write("/tmp/portal_seed.json", Path(seed_path).read_text())
        seed_args = ["--seed", "/tmp/portal_seed.json"]
    else:
        seed_args = []
    # commands.run is NOT shell-interpreted — argv goes in args.
    await sbx.commands.run(
        "python3", args=["/tmp/portal_server.py", "--port", str(port), *seed_args],
        background=True,
    )
    preview = await sbx.preview_url(port)
    return preview["url"]


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8080
    seed = args[args.index("--seed") + 1] if "--seed" in args else None
    load_seed(seed)
    print(f"FieldDesk listening on http://127.0.0.1:{port} (jobs: {len(STATE['jobs'])})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
