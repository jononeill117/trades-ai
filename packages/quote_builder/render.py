"""Render the assembled quote — Markdown + printable HTML."""

from __future__ import annotations

import html
from typing import Any


def quote_md(quote: dict[str, Any]) -> str:
    job = quote["job"]
    lines = [f"# Quote — {job.get('customer', 'customer')}",
             f"Job: {job.get('job_id','')} · Site: {job.get('site','')}\n",
             "| Item | Description | Qty | Unit | Unit price | Total |",
             "| --- | --- | --- | --- | --- | --- |"]
    for l in quote["lines"]:
        if "error" in l:
            lines.append(f"| {l['item_id']} | NOT IN PRICEBOOK | — | — | — | — |")
            continue
        lines.append(f"| `{l['item_id']}` | {l['description']} | {l['qty']} "
                     f"{l['unit']} | ${l['unit_price']:,.2f} | ${l['line_total']:,.2f} |")
    lines += ["",
              f"**Subtotal:** ${quote['subtotal']:,.2f}  ",
              f"**Tax ({quote['tax_rate']:.1%}):** ${quote['tax']:,.2f}  ",
              f"**Total:** ${quote['total']:,.2f}"]
    if quote.get("assumptions"):
        lines.append("\n## Assumptions")
        lines += [f"- {a}" for a in quote["assumptions"]]
    if quote.get("exclusions"):
        lines.append("\n## Exclusions")
        lines += [f"- {e}" for e in quote["exclusions"]]
    if quote.get("uncertain"):
        lines.append("\n## Needs a human to confirm")
        lines += [f"- `{u['item_id']}` — {u.get('reason','')}"
                  for u in quote["uncertain"]]
    lines.append("\n_Every line cites its pricebook item ID. No line was "
                 "priced by a model._")
    return "\n".join(lines) + "\n"


def quote_html(quote: dict[str, Any]) -> str:
    job = quote["job"]
    rows = "".join(
        f"<tr><td><code>{html.escape(l['item_id'])}</code></td>"
        f"<td>{html.escape(l.get('description',''))}</td>"
        f"<td>{l.get('qty','')}</td><td>${l.get('unit_price',0):,.2f}</td>"
        f"<td>${l.get('line_total',0):,.2f}</td></tr>"
        for l in quote["lines"])
    return (f"<!doctype html><meta charset='utf-8'><title>Quote "
            f"{html.escape(str(job.get('job_id','')))}</title>"
            "<style>body{font-family:system-ui;max-width:720px;margin:2em auto}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccc;"
            "padding:.4em .7em;text-align:left}</style>"
            f"<h1>Quote — {html.escape(job.get('customer',''))}</h1>"
            f"<p>Job {html.escape(str(job.get('job_id','')))} · "
            f"{html.escape(job.get('site',''))}</p>"
            f"<table><tr><th>Item</th><th>Description</th><th>Qty</th>"
            f"<th>Unit price</th><th>Total</th></tr>{rows}</table>"
            f"<p><b>Subtotal:</b> ${quote['subtotal']:,.2f}<br>"
            f"<b>Tax ({quote['tax_rate']:.1%}):</b> ${quote['tax']:,.2f}<br>"
            f"<b>Total:</b> ${quote['total']:,.2f}</p>")
