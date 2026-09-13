"""Quote report — Markdown for chat/email, printable HTML for the office."""

from __future__ import annotations

import html as _html
import time
from pathlib import Path

from .aggregate import to_quote_lines


def _money(v: float) -> str:
    return f"${v:,.2f}"


def render_markdown(quote: dict, run_id: str = "") -> str:
    lines = to_quote_lines(quote)
    out = [
        f"# Parts quote — {time.strftime('%Y-%m-%d')}",
        "",
        f"Baseline for comparison: **{quote['baseline_supplier']}**"
        + (f"   ·   run `{run_id}`" if run_id else ""),
        "",
        "| Part | Qty | Best price | Supplier | Line total | Notes |",
        "| --- | ---: | ---: | --- | ---: | --- |",
    ]
    for line in lines:
        if line.winner:
            best = _money(line.winner.unit_price)
            supplier = line.winner.supplier
        else:
            best = "—"
            supplier = "—"
        out.append(
            f"| {line.part.description} ({line.part.sku}) | {line.part.qty} | "
            f"{best} | {supplier} | {_money(line.line_total)} | {line.note} |"
        )
    out += [
        "",
        f"**Total: {_money(quote['total'])}** "
        f"(baseline {_money(quote['baseline_total'])} — "
        f"**you save {_money(quote['savings'])}**)",
        "",
    ]
    return "\n".join(out)


def render_html(quote: dict, run_id: str = "") -> str:
    lines = to_quote_lines(quote)
    rows = []
    for line in lines:
        w = line.winner
        rows.append(
            "<tr>"
            f"<td>{_html.escape(line.part.description)}<br><small>{_html.escape(line.part.sku)}</small></td>"
            f"<td class='num'>{line.part.qty}</td>"
            f"<td class='num'>{_money(w.unit_price) if w else '—'}</td>"
            f"<td>{_html.escape(w.supplier) if w else '—'}</td>"
            f"<td class='num'>{_money(line.line_total)}</td>"
            f"<td><small>{_html.escape(line.note)}</small></td>"
            "</tr>"
        )
    return f"""<!doctype html><meta charset="utf-8">
<title>Parts quote</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 2em auto; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ border-bottom: 1px solid #ddd; padding: 8px; text-align: left; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .savings {{ background: #e8f5e9; padding: 12px; font-size: 1.1em; }}
  @media print {{ body {{ margin: 0; }} }}
</style>
<h1>Parts quote</h1>
<p>Baseline: {_html.escape(quote['baseline_supplier'])} · run {_html.escape(run_id)}</p>
<table>
<tr><th>Part</th><th>Qty</th><th>Best unit</th><th>Supplier</th><th>Line</th><th>Notes</th></tr>
{''.join(rows)}
</table>
<p class="savings">Total <strong>{_money(quote['total'])}</strong> —
baseline {_money(quote['baseline_total'])} — <strong>save {_money(quote['savings'])}</strong></p>"""


def write_reports(quote: dict, run_id: str, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"quote-{run_id}.md"
    page = out_dir / f"quote-{run_id}.html"
    md.write_text(render_markdown(quote, run_id))
    page.write_text(render_html(quote, run_id))
    return md, page
