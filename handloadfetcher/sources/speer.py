"""Speer (reloadingdata.speer.com, embedded in speer.com/reloading/*-data.html).

The Handgun/Rifle listing pages actually carry **two** independent
catalogs, and the site only surfaces one of them through its own UI:

- a JSON blob (`var recipeJson`) driving the interactive picker widget --
  cartridges/bullets added or refreshed recently, maybe a few dozen.
- a much larger static list of plain `<a href=".../foo.pdf">Cartridge
  Weight</a>` links further down the same page (hundreds of entries) --
  legacy content the picker widget doesn't know about at all, but which is
  still live and linked right there in the HTML.

Both get scraped and merged into one catalog. They also use two different
PDF templates (the legacy one lacks a "Cartridge" column header and calls
its charge/velocity columns "Weight"/"Muzzle Velocity" instead of
"Grain"/"Velocity", and lists a bullet's weight in its own row rather than
inline in a "165 gr GDHP" style name) -- both are handled below.
"""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from .. import cartridges, pdf_utils
from ..http import new_session
from ..models import LoadLine, LoadTable, SourceResult

NAME = "Speer"
APP_BASE = "https://reloadingdata.speer.com"
LISTING_URLS = {
    "handgun": f"{APP_BASE}/SpeerReloading/Handgun",
    "rifle": f"{APP_BASE}/SpeerReloading/Rifle",
}
CACHE_KEY = "speer_catalog_v2"


def _extract_recipe_json(html: str) -> dict:
    i = html.find("var recipeJson")
    if i == -1:
        return {"cartridges": [], "recipes": []}
    start = html.find("{", i)
    end_marker = html.find("</script>", i)
    data, _ = json.JSONDecoder().raw_decode(html[start:end_marker])
    return data


# A legacy label reads like "30-06 Springfield 180" or "308 Winchester Gold
# Dot 150" or "300 AAC Blackout GD 150" -- the cartridge name ends right
# before the LAST whitespace-preceded number (the bullet weight in grains).
# Anything between the name and that number (e.g. "Gold Dot", "GD") is a
# bullet-type hint that ends up folded into the name; harmless for fuzzy
# matching, since a plain cartridge query is still a subset of it.
_LEGACY_WEIGHT_RE = re.compile(r"\s(\d+(?:\.\d+)?)\b")


def _legacy_cartridge_name(label: str) -> str:
    matches = list(_LEGACY_WEIGHT_RE.finditer(label))
    if not matches:
        return label
    return label[: matches[-1].start()].strip()


def list_cartridges(session=None) -> list[dict]:
    """Return [{label, recipes: [{name, pdf_url}, ...]}, ...]."""
    from .. import cache

    cached = cache.load(CACHE_KEY)
    if cached is not None:
        return cached

    s = session or new_session()
    catalog: dict[str, dict] = {}  # keyed by cartridges.normalize(label)

    def add(label: str, recipe_name: str, pdf_url: str) -> None:
        label = label.strip()
        if not label or not pdf_url:
            return
        key = cartridges.normalize(label)
        entry = catalog.setdefault(key, {"label": label, "recipes": []})
        entry["recipes"].append({"name": recipe_name, "pdf_url": pdf_url})

    for url in LISTING_URLS.values():
        resp = s.get(url, timeout=30)
        resp.raise_for_status()

        data = _extract_recipe_json(resp.text)
        value_to_label = {c["value"]: c["label"] for c in data.get("cartridges", [])}
        for r in data.get("recipes", []):
            for cval in r.get("cartridge", []):
                add(value_to_label.get(cval, cval), r.get("name", ""), r.get("pdf_url", ""))

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.lower().endswith(".pdf"):
                continue
            full_label = a.get_text(strip=True).replace("\xa0", " ").strip()
            if not full_label:
                continue
            add(_legacy_cartridge_name(full_label), full_label, href)

    items = list(catalog.values())
    cache.save(CACHE_KEY, items)
    return items


_META_PATTERNS = {
    "case": re.compile(r"Max Case Length:\s*([\d.\"]+)"),
    "coal_in": re.compile(r"Max Cart\.? OAL:\s*([\d.\"]+)"),
    "test_firearm": re.compile(r"Test Firearm:\s*(.+)"),
    "barrel": re.compile(r"Barrel Length:\s*([\d.\"]+)"),
}


def _parse_meta(text: str) -> dict:
    meta = {}
    for key, pat in _META_PATTERNS.items():
        if m := pat.search(text):
            meta[key] = m.group(1).strip()
    return meta


def _find_header(words: list[dict]):
    """Locate the load-table header, in either of Speer's two templates.

    Newer recipe PDFs: one header row -- Propellant/Cartridge/Case/Primer/
    Grain/Velocity/Grain/Velocity.
    Legacy PDFs: two stacked header rows, no "Cartridge" label at all, and
    "Weight"/"Muzzle Velocity" instead of "Grain"/"Velocity".
    """
    prop = next((w for w in words if w["text"] == "Propellant"), None)
    if prop is None:
        return None
    # A generous vertical window covers the legacy template's second header
    # line ("Muzzle Velocity" sits ~10pt above "Weight"/"Case"/"Primer").
    near = [w for w in words if abs(w["top"] - prop["top"]) < 15]
    cols = {w["text"]: w["x0"] for w in near if w["text"] in ("Case", "Primer")}
    if not {"Case", "Primer"} <= cols.keys():
        return None
    grains = sorted(w["x0"] for w in near if w["text"] in ("Grain", "Weight"))
    velocities = sorted(w["x0"] for w in near if w["text"] == "Velocity")
    if len(grains) < 2 or len(velocities) < 2:
        return None
    return {
        "top": prop["top"],
        "propellant_x0": prop["x0"],
        "case_x0": cols["Case"],
        "primer_x0": cols["Primer"],
        "charge_min_x0": grains[0],
        "velocity_min_x0": velocities[0],
        "charge_max_x0": grains[1],
        "velocity_max_x0": velocities[1],
    }


_NUM_RE = re.compile(r"^(\d+(?:\.\d+)?)$")


def _parse_load_rows(words: list[dict], header: dict) -> list[LoadLine]:
    # 2nd header line adds "(ft/sec)" under each Velocity label -- skip past it.
    body = [w for w in words if w["top"] > header["top"] + 5]
    rows = pdf_utils.cluster_rows(body, tol=2.0)
    boundaries = [
        header["propellant_x0"],
        header["case_x0"],
        header["primer_x0"],
        header["charge_min_x0"],
    ]
    num_columns = [
        ("charge_min", header["charge_min_x0"]),
        ("velocity_min", header["velocity_min_x0"]),
        ("charge_max", header["charge_max_x0"]),
        ("velocity_max", header["velocity_max_x0"]),
    ]
    loads = []
    for row in rows:
        # The powder name is always the row's leftmost content -- no lower
        # bound here, since a header label's own x0 (used to build
        # `boundaries`) can sit well to the right of where its column's
        # actual values start (seen on the legacy template). The upper
        # bound is a midpoint rather than the Case header's own x0 for the
        # same reason -- the Case column's header label and its values
        # don't line up either, so cutting at the label overshoots into
        # the name column on that template.
        name_cutoff = (boundaries[0] + boundaries[1]) / 2
        name_words = [w for w in row if w["x0"] < name_cutoff]
        if not name_words:
            continue
        name = " ".join(w["text"] for w in name_words)

        values = {}
        compressed = False
        for w in row:
            if w["x0"] < boundaries[-1] - 20:
                continue
            if w["text"].upper() == "C":
                compressed = True
                continue
            m = _NUM_RE.match(w["text"])
            if not m:
                continue
            col, col_x = min(num_columns, key=lambda c: abs(c[1] - w["x0"]))
            if abs(col_x - w["x0"]) <= 16:
                values[col] = float(m.group(1))
        if len(values) < 3:
            continue
        loads.append(
            LoadLine(
                powder=name,
                charge_min_gr=values.get("charge_min"),
                velocity_min_fps=values.get("velocity_min"),
                charge_max_gr=values.get("charge_max"),
                velocity_fps=values.get("velocity_max"),
                is_max=True,
                flags="compressed" if compressed else "",
            )
        )
    return loads


def _parse_bullet_desc(words: list[dict], header_top: float) -> tuple[str, float | None]:
    """Return (description, weight_gr) from whichever bullet-info block
    precedes the load table -- the two templates lay it out differently."""
    # Newer template: a row reads like "165 gr GDHP  4397  165  1.120 ...".
    gr_words = [w for w in words if w["text"] == "gr" and w["top"] < header_top - 10]
    if gr_words:
        row_top = gr_words[0]["top"]
        row = sorted((w for w in words if abs(w["top"] - row_top) < 3), key=lambda w: w["x0"])
        texts = [w["text"] for w in row]
        weight = None
        start = 0
        if row and re.match(r"^\d+$", texts[0]):
            weight = float(texts[0])
            start = 1
            if start < len(texts) and texts[start] == "gr":
                start += 1
        desc_words = []
        for t in texts[start:]:
            if re.match(r'^[\d.]+"?$', t):
                break
            desc_words.append(t)
        return " ".join(desc_words), weight

    # Legacy template: a "Weight (grains)" row gives the number(s); the
    # bullet name(s) are free-text lines above it (one per shared column).
    weight_hdr = next(
        (w for w in words if w["text"] == "Weight" and w["top"] < header_top - 10), None
    )
    if weight_hdr is None:
        return "", None
    value_row = [w for w in words if abs(w["top"] - weight_hdr["top"]) < 3 and re.match(r"^\d+$", w["text"])]
    weight = float(value_row[0]["text"]) if value_row else None
    name_words = [
        w["text"]
        for w in words
        if weight_hdr["top"] - 60 < w["top"] < weight_hdr["top"] - 3
        and not re.match(r'^\.?\d+(\.\d+)?"?$', w["text"])
    ]
    return " ".join(name_words), weight


def _parse_pdf(pdf_bytes: bytes, cartridge_label: str, recipe_name: str, source_url: str) -> list[LoadTable]:
    pages = pdf_utils.words_by_page(pdf_bytes)
    full_text = pdf_utils.pdftotext_layout(pdf_bytes)
    meta = _parse_meta(full_text)

    tables = []
    for page_words in pages:
        header = _find_header(page_words)
        if header is None:
            continue
        bullet_type, weight = _parse_bullet_desc(page_words, header["top"])
        loads = _parse_load_rows(page_words, header)
        if not loads:
            continue
        tables.append(
            LoadTable(
                manufacturer=NAME,
                cartridge=cartridge_label,
                bullet_weight_gr=weight,
                bullet_type=bullet_type,
                coal_in=meta.get("coal_in"),
                case=meta.get("case", ""),
                barrel=meta.get("barrel", ""),
                test_firearm=meta.get("test_firearm", ""),
                loads=loads,
                source_url=source_url,
                notes=recipe_name,
            )
        )
    return tables


def fetch(query: str, session=None) -> SourceResult:
    s = session or new_session()
    result = SourceResult(manufacturer=NAME, query=query)
    try:
        catalog = list_cartridges(s)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"could not load cartridge list: {exc}")
        return result

    matches = cartridges.best_matches(query, [c["label"] for c in catalog], limit=1)
    if not matches:
        result.errors.append("no matching cartridge found")
        return result
    label = matches[0][0]
    entry = next(c for c in catalog if c["label"] == label)
    result.matched_cartridge = label

    for recipe in entry["recipes"]:
        url = recipe["pdf_url"]
        if not url:
            continue
        full_url = url if url.startswith("http") else f"{APP_BASE}{url}"
        try:
            pdf_bytes = pdf_utils.fetch_pdf(full_url, s)
            result.tables.extend(_parse_pdf(pdf_bytes, label, recipe["name"], full_url))
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"failed to fetch/parse {full_url}: {exc}")
    if not result.tables and not result.errors:
        result.errors.append("cartridge matched but no recipes had usable PDFs")
    return result
