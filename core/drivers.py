"""Page drivers — one small interface, two implementations.

Portal and supplier adapters never talk to Playwright or Solari directly. They
drive a `PageDriver`:

    goto(url)            navigate
    fill(selector, text) type into a field
    click(selector)      click a link or button
    text(selector)       read an element's text
    content()            current page HTML
    current_url          where we are

`PlaywrightDriver` wraps a real page from a Solari cloud browser (live mode).
`HttpDriver` is a tiny stdlib "browser" — urllib + a cookie jar + an HTML
parser — good enough for the included mock portal, so the whole demo runs with
no API keys (mock mode).
"""

from __future__ import annotations

import html.parser
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PageDriver(Protocol):
    async def goto(self, url: str) -> None: ...
    async def fill(self, selector: str, value: str) -> None: ...
    async def click(self, selector: str) -> None: ...
    async def text(self, selector: str) -> str: ...
    async def content(self) -> str: ...
    @property
    def current_url(self) -> str: ...


class PlaywrightDriver:
    """Wraps a Playwright page — e.g. `await browser.new_page()` on a Solari
    cloud browser session."""

    def __init__(self, page: Any):
        self._page = page

    async def goto(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded")

    async def fill(self, selector: str, value: str) -> None:
        await self._page.fill(selector, value)

    async def click(self, selector: str) -> None:
        await self._page.click(selector)
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass  # some clicks don't navigate; fine

    async def text(self, selector: str) -> str:
        return (await self._page.locator(selector).inner_text(timeout=5000)).strip()

    async def content(self) -> str:
        return await self._page.content()

    @property
    def current_url(self) -> str:
        return self._page.url


# ---------------------------------------------------------------------------
# HttpDriver — a mini browser built on the Python standard library.
# ---------------------------------------------------------------------------

class _Page(html.parser.HTMLParser):
    """Parses a page into the bits a driver needs: forms, links, elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self.links: list[dict[str, str]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        self._form: dict[str, Any] | None = None
        self._text_stack: list[tuple[dict[str, Any], list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        el = {"tag": tag, "attrs": a, "text": ""}
        if "id" in a:
            self.by_id[a["id"]] = el
        if tag == "form":
            self._form = {"action": a.get("action", ""), "method": a.get("method", "get").upper(), "fields": {}}
        elif tag == "input" and self._form is not None:
            name = a.get("name")
            if name:
                self._form["fields"][name] = {"value": a.get("value", ""), "type": a.get("type", "text"), "id": a.get("id", "")}
        elif tag == "textarea" and self._form is not None:
            name = a.get("name")
            if name:
                self._form["fields"][name] = {"value": "", "type": "textarea", "id": a.get("id", "")}
        elif tag == "select" and self._form is not None:
            name = a.get("name")
            if name:
                self._form["fields"][name] = {"value": "", "type": "select", "id": a.get("id", "")}
        elif tag == "button" and self._form is not None:
            name = a.get("name") or a.get("id")
            if name:
                self._form["fields"][name] = {"value": a.get("value", ""), "type": "submit", "id": a.get("id", "")}
        elif tag == "option" and self._form is not None:
            # first option wins as default unless one is selected
            pass
        if tag == "a":
            self.links.append({"href": a.get("href", ""), "id": a.get("id", ""), "text": ""})
        self._text_stack.append((el, []))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None
        if self._text_stack:
            el, chunks = self._text_stack.pop()
            el["text"] = "".join(chunks).strip()
            if tag == "a" and self.links:
                self.links[-1]["text"] = el["text"]
            if self._text_stack:
                self._text_stack[-1][1].append(el["text"])

    def handle_data(self, data: str) -> None:
        if self._text_stack:
            self._text_stack[-1][1].append(data)


class HttpDriver:
    """Drives plain server-rendered pages with urllib — no browser needed.

    Supports enough of PageDriver for a simple portal: form fills, link and
    submit clicks, element text reads. Cookies persist (like a browser session)
    via a CookieJar.
    """

    def __init__(self, base_url: str = "", cookie_jar: CookieJar | None = None):
        self._base = base_url.rstrip("/")
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar or CookieJar())
        )
        self._url = ""
        self._html = ""
        self._page = _Page()
        self._values: dict[str, str] = {}  # filled form values, name -> value

    async def goto(self, url: str) -> None:
        self._url = urllib.parse.urljoin(self._base + "/", url)
        with self._opener.open(self._url) as res:
            self._html = res.read().decode("utf-8", "replace")
            self._url = res.geturl()
        self._reparse()

    async def fill(self, selector: str, value: str) -> None:
        field = self._field_name(selector)
        self._values[field] = value

    async def click(self, selector: str) -> None:
        target = self._resolve(selector)
        if target is None:
            raise LookupError(f"no element matches {selector!r} on {self._url}")
        if target["tag"] == "a":
            await self.goto(target["attrs"].get("href", ""))
            return
        # buttons / submits: POST (or GET) the enclosing form.
        form = self._form_containing(selector)
        if form is None:
            raise LookupError(f"{selector!r} is not in a form on {self._url}")
        await self._submit(form)

    async def text(self, selector: str) -> str:
        el = self._resolve(selector)
        if el is None:
            # maybe it's a form field — return its current value
            name = self._field_name(selector)
            for form in self._page.forms:
                if name in form["fields"]:
                    return self._values.get(name, form["fields"][name]["value"])
            raise LookupError(f"no element matches {selector!r} on {self._url}")
        if el["tag"] == "input":
            return el["attrs"].get("value", "")
        return el["text"]

    async def content(self) -> str:
        return self._html

    @property
    def current_url(self) -> str:
        return self._url

    # -- internals ------------------------------------------------------------

    def _reparse(self) -> None:
        self._page = _Page()
        self._page.feed(self._html)
        self._values = {}

    def _resolve(self, selector: str) -> dict[str, Any] | None:
        if selector.startswith("#"):
            return self._page.by_id.get(selector[1:])
        for link in self._page.links:
            if link["text"] == selector or link["href"] == selector:
                return {"tag": "a", "attrs": {"href": link["href"]}, "text": link["text"]}
        return None

    def _field_name(self, selector: str) -> str:
        if selector.startswith("name="):
            return selector[5:]
        if selector.startswith("#"):
            want = selector[1:]
            for form in self._page.forms:
                for name, field in form["fields"].items():
                    if field["id"] == want:
                        return name
        return selector.lstrip("#")

    def _form_containing(self, selector: str) -> dict[str, Any] | None:
        if selector.startswith("#"):
            want = selector[1:]
            for form in self._page.forms:
                for field in form["fields"].values():
                    if field["id"] == want or field["type"] in ("submit", "button"):
                        # submit buttons may have an id but no name
                        return form
            return None
        name = self._field_name(selector)
        for form in self._page.forms:
            if name in form["fields"]:
                return form
        return None

    async def _submit(self, form: dict[str, Any]) -> None:
        data = {name: f["value"] for name, f in form["fields"].items() if f["type"] != "submit"}
        data.update(self._values)
        action = urllib.parse.urljoin(self._url, form["action"] or self._url)
        if form["method"] == "POST":
            body = urllib.parse.urlencode(data).encode()
            with self._opener.open(action, body) as res:
                self._html = res.read().decode("utf-8", "replace")
                self._url = res.geturl()
        else:
            url = action + ("&" if "?" in action else "?") + urllib.parse.urlencode(data)
            with self._opener.open(url) as res:
                self._html = res.read().decode("utf-8", "replace")
                self._url = res.geturl()
        self._reparse()


def as_driver(page: Any) -> PageDriver:
    """Normalize whatever `browser()` returned into a PageDriver: pass through
    drivers (mock mode) and wrap Playwright pages (live mode)."""
    if isinstance(page, PageDriver):
        return page
    return PlaywrightDriver(page)


class FixtureDriver:
    """Serves cached HTML pages instead of fetching — procurement mock mode.

    `pages` maps a key (supplier name, or a URL substring) to HTML. `goto`
    picks the first page whose key appears in the URL. Click/fill are no-ops —
    fixture pages are read-only.
    """

    def __init__(self, pages: dict[str, str]):
        self._pages = pages
        self._url = ""
        self._html = ""

    async def goto(self, url: str) -> None:
        for key, html_text in self._pages.items():
            if key in url:
                self._url, self._html = url, html_text
                return
        raise LookupError(f"no fixture page matches {url!r} (have: {list(self._pages)})")

    async def fill(self, selector: str, value: str) -> None:
        pass

    async def click(self, selector: str) -> None:
        pass

    async def text(self, selector: str) -> str:
        raise NotImplementedError("FixtureDriver only supports goto() and content()")

    async def content(self) -> str:
        return self._html

    @property
    def current_url(self) -> str:
        return self._url
