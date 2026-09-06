"""Renders a query's results as a single self-contained HTML report and
opens it in the default browser -- a technical-reference-sheet look, meant
to be glanced at on a phone at the bench or printed and kept in a binder.

Everything is inlined (no CDN, no external assets): this has to work fully
offline, same as the rest of this repo.
"""
from __future__ import annotations

import datetime
import html
import re
import webbrowser
from pathlib import Path

from . import __author__, __author_url__, __title__
from .models import LoadTable, SourceResult

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# One accent color per manufacturer, purely so a long report stays visually
# scannable -- falls back to a neutral slate for anything not listed here
# (e.g. Hodgdon, once it exists).
_ACCENTS = {
    "Nosler": "#7a1f1f",
    "Sierra": "#8a5a20",
    "Speer": "#1f5c8a",
    "Barnes": "#3a6b35",
    "Hammer Bullets": "#5a3d7a",
}
_DEFAULT_ACCENT = "#44495a"


def _esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "query"


def _anchor(*parts: str) -> str:
    return _slugify(" ".join(parts))


def _spec_list(table: LoadTable) -> str:
    """The small "datasheet" line of case/primer/barrel/twist/COAL, only
    showing whichever fields this source actually supplied."""
    fields = [
        ("Case", table.case),
        ("Primer", table.primer),
        ("Barrel", table.barrel),
        ("Twist", table.twist),
        ("COAL", table.coal_in),
        ("Test firearm", table.test_firearm),
    ]
    items = [f"<span><b>{_esc(k)}:</b> {_esc(v)}</span>" for k, v in fields if v]
    if not items:
        return ""
    return f'<div class="spec-list">{"".join(items)}</div>'


def _flag_badges(flags: str) -> str:
    if not flags:
        return ""
    badges = []
    for flag in (f.strip() for f in flags.split(",")):
        if not flag:
            continue
        cls = "badge"
        if "compressed" in flag.lower():
            cls += " badge-amber"
        elif "accurate" in flag.lower():
            cls += " badge-green"
        badges.append(f'<span class="{cls}">{_esc(flag)}</span>')
    return "".join(badges)


def _load_table_html(table: LoadTable) -> str:
    bullet_bits = []
    if table.bullet_weight_gr:
        bullet_bits.append(f"{table.bullet_weight_gr:g} gr")
    if table.bullet_type:
        bullet_bits.append(_esc(table.bullet_type))
    bullet_desc = " ".join(bullet_bits) or "&nbsp;"

    rows = []
    for ld in table.loads:
        max_cls = ' class="is-max"' if ld.is_max else ""
        rows.append(
            "<tr>"
            f"<td>{_esc(ld.powder)}</td>"
            f'<td class="num"{max_cls}>{_esc(ld.charge_display())}</td>'
            f'<td class="num">{_esc(ld.velocity_display())}</td>'
            f"<td>{_flag_badges(ld.flags)}</td>"
            "</tr>"
        )

    source_link = (
        f'<a class="source-link" href="{_esc(table.source_url)}" target="_blank" rel="noopener">source ↗</a>'
        if table.source_url
        else ""
    )
    notes = f'<p class="notes">{_esc(table.notes)}</p>' if table.notes else ""

    return f"""
    <div class="load-table">
      <div class="load-table-head">
        <h4>{bullet_desc}</h4>
        {source_link}
      </div>
      {_spec_list(table)}
      {notes}
      <table>
        <thead><tr><th>Powder</th><th class="num">Charge</th><th class="num">Velocity</th><th>Flags</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>
    """


def _manufacturer_section(result: SourceResult) -> str:
    accent = _ACCENTS.get(result.manufacturer, _DEFAULT_ACCENT)
    anchor = _anchor(result.manufacturer)

    if not result.tables:
        reason = "; ".join(result.errors) or "no data returned"
        return f"""
        <section class="mfr" id="{anchor}" style="--accent: {accent}">
          <h2>{_esc(result.manufacturer)}</h2>
          <p class="empty">No data — {_esc(reason)}.</p>
        </section>
        """

    cartridge = _esc(result.matched_cartridge or result.query)
    tables_html = "".join(_load_table_html(t) for t in result.tables)
    errors_html = (
        f'<p class="warn">{_esc("; ".join(result.errors))}</p>' if result.errors else ""
    )
    return f"""
    <section class="mfr" id="{anchor}" style="--accent: {accent}">
      <h2>{_esc(result.manufacturer)}</h2>
      <p class="cartridge-name">{cartridge}</p>
      {errors_html}
      {tables_html}
    </section>
    """


def _nav_html(results: list[SourceResult]) -> str:
    items = []
    for r in results:
        count = sum(len(t.loads) for t in r.tables)
        state = "" if count else " class=\"empty-link\""
        items.append(
            f'<a href="#{_anchor(r.manufacturer)}"{state}>{_esc(r.manufacturer)}'
            f'<span class="nav-count">{count}</span></a>'
        )
    return "\n".join(items)


_CSS = """
:root {
  color-scheme: light;
  --bg: #f4f5f7;
  --panel: #ffffff;
  --ink: #1c1f26;
  --muted: #6b7280;
  --line: #e2e4ea;
  --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--sans);
  background: var(--bg);
  color: var(--ink);
  line-height: 1.45;
}
header.banner {
  background: #14171f;
  color: #f4f5f7;
  padding: 28px clamp(16px, 4vw, 48px);
}
header.banner .brand {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #6f7690;
}
header.banner .brand a {
  color: #6f7690;
  text-decoration: none;
  font-weight: 500;
  letter-spacing: normal;
  text-transform: none;
  font-family: var(--mono);
  font-size: 0.75rem;
}
header.banner .brand a:hover { color: #9aa1b2; }
header.banner h1 {
  margin: 0 0 4px;
  font-size: 1.5rem;
  font-weight: 650;
  letter-spacing: -0.01em;
}
header.banner .meta {
  color: #9aa1b2;
  font-size: 0.85rem;
  font-family: var(--mono);
}
nav.toc {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 12px clamp(16px, 4vw, 48px);
  background: #1c202b;
  border-bottom: 1px solid #2a2f3d;
  position: sticky;
  top: 0;
  z-index: 10;
}
nav.toc a {
  color: #d7dae2;
  text-decoration: none;
  font-size: 0.82rem;
  padding: 5px 11px;
  border-radius: 999px;
  background: #262b38;
  border: 1px solid #333a4a;
  white-space: nowrap;
}
nav.toc a:hover { background: #313747; }
nav.toc a.empty-link { opacity: 0.45; }
nav.toc .nav-count {
  font-family: var(--mono);
  color: #8f96a8;
  margin-left: 6px;
}
main {
  max-width: 980px;
  margin: 0 auto;
  padding: clamp(16px, 4vw, 40px);
}
section.mfr {
  background: var(--panel);
  border: 1px solid var(--line);
  border-left: 4px solid var(--accent, var(--muted));
  border-radius: 10px;
  padding: 20px clamp(16px, 3vw, 28px);
  margin-bottom: 22px;
  scroll-margin-top: 56px;
}
section.mfr h2 {
  margin: 0;
  font-size: 1.15rem;
  color: var(--accent, var(--ink));
}
p.cartridge-name {
  margin: 2px 0 16px;
  font-size: 0.95rem;
  color: var(--muted);
  font-family: var(--mono);
}
p.empty { color: var(--muted); font-style: italic; margin: 8px 0 0; }
p.warn {
  background: #fff6e5;
  border: 1px solid #f2d8a0;
  color: #7a5a10;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 0.85rem;
}
.load-table {
  border-top: 1px solid var(--line);
  padding-top: 14px;
  margin-top: 14px;
}
.load-table:first-of-type { border-top: none; margin-top: 4px; }
.load-table-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.load-table-head h4 {
  margin: 0 0 6px;
  font-size: 1rem;
}
a.source-link {
  font-size: 0.78rem;
  color: var(--muted);
  text-decoration: none;
  white-space: nowrap;
}
a.source-link:hover { color: var(--accent); }
.spec-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
  font-size: 0.82rem;
  color: var(--muted);
  margin-bottom: 10px;
  font-family: var(--mono);
}
.spec-list b { color: var(--ink); font-weight: 600; }
p.notes { font-size: 0.85rem; color: var(--muted); margin: 0 0 8px; }
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}
th, td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--line);
  text-align: left;
}
th {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
  font-weight: 600;
}
td.num, th.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: #fafafa; }
td.num.is-max { color: #a3271f; font-weight: 700; }
.badge {
  display: inline-block;
  font-size: 0.7rem;
  padding: 1px 7px;
  border-radius: 999px;
  background: #eceef2;
  color: #4a4f5c;
  margin-right: 4px;
}
.badge-amber { background: #fdecc8; color: #7a5a10; }
.badge-green { background: #d9f0dd; color: #206030; }
footer {
  max-width: 980px;
  margin: 0 auto;
  padding: 8px clamp(16px, 4vw, 40px) 40px;
  color: var(--muted);
  font-size: 0.78rem;
}
footer .disclaimer {
  border-top: 1px solid var(--line);
  padding-top: 14px;
  margin-top: 6px;
}
footer .credit {
  margin-top: 10px;
  font-size: 0.72rem;
}
footer .credit a { color: var(--muted); }
@media print {
  nav.toc { display: none; }
  header.banner { background: none; color: var(--ink); border-bottom: 2px solid var(--ink); }
  header.banner .meta { color: var(--muted); }
  header.banner .brand, header.banner .brand a { color: var(--muted); }
  section.mfr { break-inside: avoid; border-left-width: 3px; box-shadow: none; }
  .load-table { break-inside: avoid; }
  body { background: #fff; }
}
"""


def render_html(query: str, results: list[SourceResult]) -> str:
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    total_loads = sum(len(t.loads) for r in results for t in r.tables)
    total_sources = sum(1 for r in results if r.tables)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(__title__)} — {_esc(query)}</title>
<style>{_CSS}</style>
</head>
<body>
<header class="banner">
  <div class="brand">{_esc(__title__)} <a href="{_esc(__author_url__)}" target="_blank" rel="noopener">by {_esc(__author__)}</a></div>
  <h1>{_esc(query)}</h1>
  <div class="meta">generated {generated} · {total_loads} load lines across {total_sources} source(s)</div>
</header>
<nav class="toc">{_nav_html(results)}</nav>
<main>
{"".join(_manufacturer_section(r) for r in results)}
</main>
<footer>
  <div class="disclaimer">
    Data is scraped directly from each manufacturer's own published load
    data and reformatted here for comparison; it is not independently
    verified. Always cross-check against the current data on the
    manufacturer's own site before loading, start at the starting/minimum
    charge and work up while watching for pressure signs, and never exceed
    a published maximum. Component lots, brass, primers, and firearms all
    vary — published data is a reference, not a substitute for your own
    careful work-up.
  </div>
  <div class="credit">{_esc(__title__)} · by <a href="{_esc(__author_url__)}" target="_blank" rel="noopener">{_esc(__author__)}</a></div>
</footer>
</body>
</html>
"""


def write_report(query: str, results: list[SourceResult], out_dir: Path | None = None) -> Path:
    out_dir = Path(out_dir) if out_dir else REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{_slugify(query)}-{timestamp}.html"
    path.write_text(render_html(query, results), encoding="utf-8")
    return path


def open_in_browser(path: Path) -> None:
    webbrowser.open(path.resolve().as_uri())
