"""Hammer Bullets (hammerbullets.com/load-data/).

Two quirks, in order:

1. The whole site sits behind an age-gate plugin that serves a stripped
   page (no load-data content at all) until a cookie is set. The gate
   accepts a plain POST of `age_gate[confirm]=1` back to the same URL and
   redirects once `age_gate=18` is set -- no real verification.
2. Load data itself isn't hosted as PDFs but as per-cartridge Google
   Sheets, linked from a big table on the (unlocked) load-data page. Each
   sheet is fetched via its CSV export endpoint. The sheet is a print
   layout, not a data table: several bullets' blocks are stacked one after
   another, each repeating boilerplate disclaimer rows, a cartridge-meta
   row, one-or-more bullet-meta rows, a "Powder/Start/Fill %/F.P.S./
   /Velocity at Max" header, then its data rows -- so the CSV is parsed as
   a small state machine rather than a flat table.
"""
from __future__ import annotations

import csv
import io
import re

from bs4 import BeautifulSoup

from .. import cartridges
from ..http import new_session
from ..models import LoadLine, LoadTable, SourceResult

NAME = "Hammer Bullets"
BASE_URL = "https://hammerbullets.com"
LOAD_DATA_URL = f"{BASE_URL}/load-data/"
CACHE_KEY = "hammer_cartridges"

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def _pass_age_gate(session) -> None:
    resp = session.get(LOAD_DATA_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    age_input = soup.find("input", attrs={"name": "age_gate[age]"})
    data = {"age_gate[confirm]": "1", "age_gate[lang]": "en"}
    if age_input and age_input.get("value"):
        data["age_gate[age]"] = age_input["value"]
    session.post(LOAD_DATA_URL, data=data, timeout=30)


def list_cartridges(session=None) -> list[dict]:
    from .. import cache

    cached = cache.load(CACHE_KEY)
    if cached is not None:
        return cached

    s = session or new_session()
    _pass_age_gate(s)
    resp = s.get(LOAD_DATA_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    for a in soup.find_all("a", href=True):
        m = _SHEET_ID_RE.search(a["href"])
        label = a.get_text(strip=True)
        if not m or not label:
            continue
        items.append({"label": label, "sheet_id": m.group(1)})

    cache.save(CACHE_KEY, items)
    return items


def _csv_export_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"


_DISCLAIMER_RE = re.compile(r"always start low|not responsible|for your load process", re.I)
_FOOTNOTE_RE = re.compile(r"^\s*[*√]|shorter than saami|minimal coal published", re.I)


def _clean(cell: str) -> str:
    return (cell or "").strip()


def _parse_value_with_flags(cell: str):
    """'3210' -> (3210.0, ''); '2870 c•' -> (2870.0, 'compressed, long drop tube')."""
    cell = _clean(cell)
    m = re.match(r"([\d.]+)\s*(.*)$", cell)
    if not m:
        return None, ""
    flags = []
    tail = m.group(2)
    if "c" in tail.lower():
        flags.append("compressed")
    if "•" in tail:
        flags.append("long drop tube")
    return float(m.group(1)), ", ".join(flags)


def _parse_csv(text: str, cartridge_label: str, source_url: str) -> list[LoadTable]:
    rows = list(csv.reader(io.StringIO(text)))
    tables: list[LoadTable] = []

    cartridge_meta = {"case": "", "barrel": ""}
    pending_bullets: list[dict] = []
    current_table: LoadTable | None = None

    def flush():
        nonlocal current_table
        if current_table is not None and current_table.loads:
            tables.append(current_table)
        current_table = None

    for row in rows:
        cells = [_clean(c) for c in row]
        # pad to a predictable width
        while len(cells) < 7:
            cells.append("")
        _, col1, col2, col3, col4, col5, col6 = cells[:7]

        if not any(cells) or _DISCLAIMER_RE.search(col1) or _FOOTNOTE_RE.match(col1):
            continue

        if "barrel length" in col2.lower():
            # cartridge-meta row, e.g. "22 Hornet | Barrel Length 24" | | Case 1.403" | SAAMI COAL 1.723""
            flush()
            pending_bullets = []
            cartridge_meta["case"] = re.sub(r"(?i)case\s*", "", col5).strip()
            cartridge_meta["barrel"] = re.sub(r"(?i)barrel length\s*", "", col2).strip()
            continue

        if "twist rate" in col2.lower():
            pending_bullets.append(
                {
                    "name": col1.lstrip("†").strip(),
                    "twist": re.sub(r"(?i)twist rate or faster\s*", "", col2).strip(),
                    "bc": col4,
                    "coal": re.sub(r"[√*]", "", col5).strip(),
                }
            )
            continue

        if col1 == "Powder" and col2 == "Start":
            flush()
            bullet = pending_bullets[0] if pending_bullets else {}
            current_table = LoadTable(
                manufacturer=NAME,
                cartridge=cartridge_label,
                bullet_type=bullet.get("name", ""),
                case=cartridge_meta["case"],
                barrel=cartridge_meta["barrel"],
                twist=bullet.get("twist", ""),
                coal_in=bullet.get("coal") or None,
                source_url=source_url,
                notes=(f"BC {bullet['bc']}" if bullet.get("bc") else ""),
            )
            if m := re.match(r"(\d+(?:\.\d+)?)", bullet.get("name", "")):
                current_table.bullet_weight_gr = float(m.group(1))
            pending_bullets = []
            continue

        if current_table is not None and col1:
            start_gr, _ = _parse_value_with_flags(col2)
            start_fps, _ = _parse_value_with_flags(col4)
            max_fps, flags = _parse_value_with_flags(col6)
            if start_gr is None and max_fps is None:
                continue
            current_table.loads.append(
                LoadLine(
                    powder=col1,
                    charge_min_gr=start_gr,
                    velocity_min_fps=start_fps,
                    velocity_fps=max_fps,
                    is_max=True,
                    flags=flags,
                )
            )

    flush()
    return tables


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

    url = _csv_export_url(entry["sheet_id"])
    try:
        resp = s.get(url, timeout=30)
        resp.raise_for_status()
        # Google's CSV export is always UTF-8, but often without a charset
        # in its Content-Type header -- left to guess, requests/chardet can
        # misdetect it as Latin-1, mangling the odd "†"/"√"/"®" a sheet uses
        # into mojibake ("Â¡", "â "...). Decode explicitly instead of
        # trusting resp.text's guess.
        csv_text = resp.content.decode("utf-8")
        result.tables = _parse_csv(csv_text, label, url)
        if not result.tables:
            result.errors.append("sheet fetched but no load tables had data yet (Hammer marks some as not-yet-published)")
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"failed to fetch/parse {url}: {exc}")
    return result
