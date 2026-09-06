"""Nosler (nosler.com/load-data/caliber-and-cartridge-data.html).

The listing page links each cartridge to its own page (e.g.
nosler.com/9mm-luger-parabellum), which in turn has one tab per bullet
weight, each linking a small, clean, text-based PDF. Table rows repeat a
powder's charge/velocity/load-density three times (starting, near-max,
max) but only print the powder name and the "MAX." marker on the first of
the three -- so a name is remembered across rows until the next "MAX." row
starts a new one, otherwise a stray footnote occasionally printed in that
column (e.g. "Most Accurate Powder Tested") gets mistaken for a name.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .. import cartridges, pdf_utils
from ..http import new_session
from ..models import LoadLine, LoadTable, SourceResult

NAME = "Nosler"
LOAD_DATA_URL = "https://www.nosler.com/load-data/caliber-and-cartridge-data.html"
CACHE_KEY = "nosler_cartridges"


def list_cartridges(session=None) -> list[dict]:
    from .. import cache

    cached = cache.load(CACHE_KEY)
    if cached is not None:
        return cached

    s = session or new_session()
    resp = s.get(LOAD_DATA_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    for container in soup.find_all("div", class_="load-data-tab-container"):
        for a in container.find_all("a", href=True):
            label = a.get_text(strip=True)
            href = a["href"]
            if not label or not href.startswith("http"):
                continue
            items.append({"label": label, "url": href})

    cache.save(CACHE_KEY, items)
    return items


def list_bullet_pages(cartridge_url: str, session=None) -> list[dict]:
    """Return [{name: "115 Grain", pdf_url: ...}, ...] for one cartridge page."""
    s = session or new_session()
    resp = s.get(cartridge_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tabs = []
    for tab in soup.find_all(attrs={"data-tab-name": True}):
        a = tab.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if not href.lower().endswith(".pdf"):
            continue
        url = href if href.startswith("http") else f"https://www.nosler.com{href}"
        tabs.append({"name": tab.get("data-tab-name", ""), "pdf_url": url})
    return tabs


_META_PATTERNS = {
    "case": re.compile(r"CASE TYPE:\s*(\S+)"),
    "primer": re.compile(r"PRIMER TYPE\s+(\S.*?)\s*$", re.M),
    "barrel": re.compile(r"BARREL Length/Make\s+(\S.*?)\s*$", re.M),
    "twist": re.compile(r"BARREL Twist\s+(\S.*?)\s*$", re.M),
    "coal_in": re.compile(r"TESTED O\.A\.C\.L\.\s+(\S.*?)\s*$", re.M),
}

_ROW_RE = re.compile(
    r'^\s*(?P<name>[A-Za-z][A-Za-z0-9 .#\-®]*?)?\s{2,}'
    r'(?P<charge>\d+\.?\d*)\s+(?P<flags>\*{0,2}\s*(?:MAX\.)?)\s*'
    r'(?P<vel>\d+)\s+.*?(?P<density>\d+)%\s*$'
)


def _parse_meta(text: str) -> dict:
    meta = {}
    for key, pat in _META_PATTERNS.items():
        if m := pat.search(text):
            meta[key] = m.group(1).strip()
    return meta


def _parse_rows(text: str) -> list[LoadLine]:
    loads = []
    current_name = None
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        is_max = "MAX." in m.group("flags")
        raw_name = (m.group("name") or "").strip()
        if is_max and raw_name:
            current_name = raw_name
        if current_name is None:
            continue
        loads.append(
            LoadLine(
                powder=current_name,
                charge_gr=float(m.group("charge")),
                velocity_fps=float(m.group("vel")),
                is_max=is_max,
                flags="most accurate" if "*" in m.group("flags") else "",
            )
        )
    return loads


def _parse_pdf(pdf_bytes: bytes, cartridge_label: str, bullet_name: str, source_url: str) -> LoadTable | None:
    text = pdf_utils.pdftotext_layout(pdf_bytes)
    meta = _parse_meta(text)
    loads = _parse_rows(text)
    if not loads:
        return None
    weight = None
    if m := re.match(r"(\d+(?:\.\d+)?)", bullet_name):
        weight = float(m.group(1))
    # The tab is often just the weight spelled out ("115 Grain") with no
    # separate type info -- drop it as a type label once weight is captured.
    bullet_type = "" if re.fullmatch(r"\d+(?:\.\d+)?\s*Grains?", bullet_name, re.I) else bullet_name
    return LoadTable(
        manufacturer=NAME,
        cartridge=cartridge_label,
        bullet_weight_gr=weight,
        bullet_type=bullet_type,
        coal_in=meta.get("coal_in"),
        case=meta.get("case", ""),
        primer=meta.get("primer", ""),
        barrel=meta.get("barrel", ""),
        twist=meta.get("twist", ""),
        loads=loads,
        source_url=source_url,
    )


def fetch(query: str, session=None) -> SourceResult:
    s = session or new_session()
    result = SourceResult(manufacturer=NAME, query=query)
    try:
        listing = list_cartridges(s)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"could not load cartridge list: {exc}")
        return result

    matches = cartridges.best_matches(query, [i["label"] for i in listing], limit=1)
    if not matches:
        result.errors.append("no matching cartridge found")
        return result
    label = matches[0][0]
    entry = next(i for i in listing if i["label"] == label)
    result.matched_cartridge = label

    try:
        bullet_pages = list_bullet_pages(entry["url"], s)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"failed to load cartridge page {entry['url']}: {exc}")
        return result

    for bp in bullet_pages:
        try:
            pdf_bytes = pdf_utils.fetch_pdf(bp["pdf_url"], s)
            table = _parse_pdf(pdf_bytes, label, bp["name"], bp["pdf_url"])
            if table:
                result.tables.append(table)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"failed to fetch/parse {bp['pdf_url']}: {exc}")
    if not result.tables and not result.errors:
        result.errors.append("cartridge matched but no bullet-weight PDFs had usable data")
    return result
