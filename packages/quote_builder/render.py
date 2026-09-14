"""Render the assembled quote — a complete Markdown document + HTML."""

from __future__ import annotations

import html
from typing import Any


def quote_md(quote: dict[str, Any]) -> str:
    job = quote["job"]
    terms = quote.get("terms", {})
    L = [f"# Quote — {job.get('customer', 'customer')}",
         f"Job: {job.get('job_id','')} · Site: {job.get('site','')}", "",
         f"_{job.get('description','')}_", "",
         "## Line items",
         "| Item | Description | Qty | Unit | Unit price | Total |",
         "| --- | --- | --- | --- | --- | --- |"]
    for l in quote["lines"]:
        if "error" in l:
            L.append(f"| {l['item']} | NO PRICE HISTORY | — | — | — | — |")
            continue
        L.append(f"| `{l['item']}` | {l['description']} | {l['qty']} "
                 f"{l['unit']} | ${l['unit_price']:,.2f} | ${l['line_total']:,.2f} |")
    L += ["",
          f"**Subtotal:** ${quote['subtotal']:,.2f}  ",
          f"**Tax ({quote['tax_rate']:.1%}):** ${quote['tax']:,.2f}  ",
          f"**Total:** ${quote['total']:,.2f}", "",
          "Each price is the median of what this shop has charged for the "
          "same item — the band and the historical jobs behind it are shown "
          "for the approver's review:",
          ""]
    for l in quote["lines"]:
        if "error" in l:
            continue
        L.append(f"- `{l['item']}` ${l['unit_price']:,.2f} — inside "
                 f"${l['band']['lo']:,.2f}–${l['band']['hi']:,.2f}, "
                 f"from {', '.join(l['priced_from'])}")

    if quote.get("uncertain"):
        L.append("\n## Options a human must confirm")
        L += [f"- `{u['item']}` — {u.get('reason','')}"
              for u in quote["uncertain"]]

    if quote.get("inclusions"):
        L.append("\n## Scope — what's included")
        L += [f"- {i}" for i in quote["inclusions"]]
    L.append("\n## Scope — what's NOT included")
    L += [f"- {e}" for e in quote["exclusions"]]

    if quote.get("exploratory"):
        L.append("\n## What we can't know until work starts")
        L += [f"- {e}" for e in quote["exploratory"]]
    if quote.get("concerns"):
        L.append("\n## Concerns & risks")
        L += [f"- {c}" for c in quote["concerns"]]

    L.append("\n## If the job grows")
    L.append(terms.get("escalation_policy",
             "Any work beyond the scope above is priced and approved in "
             "writing before it starts — nothing is added to the invoice "
             "without your sign-off."))
    L.append("\n## Terms")
    for k in ("deposit", "payment", "validity"):
        if terms.get(k):
            L.append(f"- **{k.capitalize()}:** {terms[k]}")
    if terms.get("tos"):
        L.append(f"- {terms['tos']}")

    L.append("\n_Every line is priced from this shop's quote history and "
             "cites the jobs behind it. No line was priced by a model._")
    return "\n".join(L) + "\n"


def quote_html(quote: dict[str, Any]) -> str:
    job = quote["job"]
    esc = html.escape
    rows = "".join(
        f"<tr><td><code>{esc(l['item'])}</code></td>"
        f"<td>{esc(l.get('description',''))}</td>"
        f"<td>{l.get('qty','')}</td><td>${l.get('unit_price',0):,.2f}</td>"
        f"<td>${l.get('line_total',0):,.2f}</td></tr>"
        for l in quote["lines"])

    def ul(items):
        return "<ul>" + "".join(f"<li>{esc(str(i))}</li>" for i in items) + "</ul>"

    terms = quote.get("terms", {})
    terms_html = ul([f"{k.capitalize()}: {v}" for k, v in terms.items() if v])
    return (f"<!doctype html><meta charset='utf-8'><title>Quote "
            f"{esc(str(job.get('job_id','')))}</title>"
            "<style>body{font-family:system-ui;max-width:760px;margin:2em auto}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccc;"
            "padding:.4em .7em;text-align:left}h2{margin-top:1.4em}</style>"
            f"<h1>Quote — {esc(job.get('customer',''))}</h1>"
            f"<p>Job {esc(str(job.get('job_id','')))} · "
            f"{esc(job.get('site',''))}</p>"
            f"<p><i>{esc(job.get('description',''))}</i></p>"
            f"<h2>Line items</h2><table><tr><th>Item</th><th>Description</th>"
            f"<th>Qty</th><th>Unit price</th><th>Total</th></tr>{rows}</table>"
            f"<p><b>Subtotal:</b> ${quote['subtotal']:,.2f}<br>"
            f"<b>Tax ({quote['tax_rate']:.1%}):</b> ${quote['tax']:,.2f}<br>"
            f"<b>Total:</b> ${quote['total']:,.2f}</p>"
            f"<h2>Included</h2>{ul(quote.get('inclusions', []))}"
            f"<h2>Not included</h2>{ul(quote.get('exclusions', []))}"
            f"<h2>Unknown until work starts</h2>{ul(quote.get('exploratory', []))}"
            f"<h2>Concerns &amp; risks</h2>{ul(quote.get('concerns', []))}"
            f"<h2>If the job grows</h2><p>{esc(terms.get('escalation_policy',''))}</p>"
            f"<h2>Terms</h2>{terms_html}")
