# Adapter-authoring guide

Two adapter kinds ship in this repo. Both follow the same rule: **adapters
never import Solari** — they drive a `PageDriver` (core/drivers.py) or parse
HTML, so they work identically in mock and live modes.

## Portal adapters (dispatch)

Interface — `packages/dispatch/portals/base.py`:

```python
async def login(driver) -> bool
async def create_job(driver, order, slot) -> str   # returns portal job ref/URL
async def confirm_booking(driver, ref) -> str      # returns final status
```

- Raise `NeedsDesktopError` from any step that genuinely can't run in a
  browser — the orchestrator falls back to a Solari desktop automatically.
- The bundled `MockPortalAdapter` (against FieldDesk, our fictional demo
  portal) is the reference implementation — copy its shape.
- `SelectorAdapter` does the whole flow from a selector map:
  `config/portals.<name>.yaml` holds `base_url` + CSS selectors, so adapting
  to your portal is config, not code. The shipped ServiceTitan / Housecall
  Pro / Jobber adapters are community examples: real interface, placeholder
  selectors.

## Supplier adapters (procurement)

Subclass `SupplierAdapter` (`packages/procurement/suppliers/base.py`):

```python
class MySupplier(SupplierAdapter):
    name = "mysupplier"
    base_url = "https://..."
    search_path = "/search?q={q}"
    card_re = r'<div class="product"[^>]*>.*?</div>'   # tune to their markup
    login_re = r"sign in .{0,30}price"                  # login-gate detector
    oos_re = r"out of stock"
```

Then register it in `suppliers/__init__.py` and enable it in
`config/suppliers.yaml`. Return `Offer(login_required=True)` when pricing is
behind a login — never fake a price.

## Logging in once (browser profiles + handoff)

The intended auth pattern — no credentials in the repo, ever:

1. Create a Solari profile: it stores cookies + localStorage server-side.
2. First run: the session isn't logged in → mint a **handoff link**
   (`core.login_handoff_url(session_id, reason)`) — a human opens it, sees the
   real cloud browser, signs in (2FA and all), control returns to the agent.
3. `core.save_profile_from_session()` persists that login into the profile.
4. Every later run passes `profile_name` to `core.browser(...)` and starts
   already signed in.

Config keys `portal_profile` / `suppliers.<name>.profile` name the profile
each adapter should attach.
