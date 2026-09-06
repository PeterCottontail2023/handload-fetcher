"""Barnes Bullets (barnesbullets.com/load-data/).

The load-data page lists every cartridge as a link to a per-cartridge PDF.
The PDFs come in two layouts:

- "handgun" style: one or more `Bullet Weight: N gr` sections, each with a
  clean 5-column table (Powder / min charge / min velocity / max charge /
  max velocity).
- "rifle" style: a cover page (case/primer/barrel/twist), then one page per
  bullet weight with a sidebar bullet description and the same 5-column
  table, plus inline flags: a leading "*" on a powder name means "most
  accurate load tested", a trailing "c" on a charge means "compressed".

Neither has real table gridlines, so columns are recovered by matching
each number's x-position to the header's column positions.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .. import cartridges, pdf_utils
from ..http import new_session
from ..models import LoadLine, LoadTable, SourceResult

NAME = "Barnes"
LOAD_DATA_URL = "https://barnesbullets.com/load-data/"
CACHE_KEY = "barnes_cartridges"


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
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf") and "/barnes-loaddata/" in href.lower():
            label = a.get_text(strip=True)
            # Screen-reader link text is appended straight onto many labels,
            # e.g. "357 SIG- This link will open a PDF document".
            label = re.split(r"-?\s*This link will open", label, flags=re.I)[0].strip()
            if not label or "recall" in href.lower():
                continue
            url = href if href.startswith("http") else f"https://barnesbullets.com{href}"
            items.append({"label": label, "url": url})

    cache.save(CACHE_KEY, items)
    return items


_NUM_RE = re.compile(r"^(\d+(?:\.\d+)?)([a-zA-Z*]*)$")


def _parse_number(word: str):
    """Split a table cell like '30.0c' into (30.0, 'c') or return (None, None)."""
    m = _NUM_RE.match(word.lstrip("*"))
    if not m:
        return None, None
    return float(m.group(1)), m.group(2)


def _find_all_headers(words: list[dict]) -> list[tuple[list[tuple[str, float]], float]]:
    """Locate every Powder/Charge/Velocity header block in a (possibly
    multi-section) page or document, each with its own column x-positions.

    A single PDF page can hold more than one bullet's table stacked
    vertically (e.g. two 150gr bullet variants side by side in the source
    layout), each with its own header -- so this returns all of them,
    top-to-bottom, rather than assuming there is exactly one.
    """
    powder_words = sorted((w for w in words if w["text"] == "Powder"), key=lambda w: w["top"])
    charge_words = [w for w in words if w["text"] == "Charge"]
    velocity_words = [w for w in words if w["text"] == "Velocity"]
    headers = []
    for pw in powder_words:
        near_charge = sorted(
            (w for w in charge_words if abs(w["top"] - pw["top"]) < 20), key=lambda w: w["x0"]
        )
        near_vel = sorted(
            (w for w in velocity_words if abs(w["top"] - pw["top"]) < 20), key=lambda w: w["x0"]
        )
        if len(near_charge) >= 2 and len(near_vel) >= 2:
            columns = [
                ("charge_min", near_charge[0]["x0"]),
                ("velocity_min", near_vel[0]["x0"]),
                ("charge_max", near_charge[1]["x0"]),
                ("velocity_max", near_vel[1]["x0"]),
            ]
            headers.append((columns, pw["top"]))
    return headers


_COL_TOLERANCE = 15.0  # max px a number can drift from its column and still count


def _dedupe_by_position(row: list[dict]) -> list[dict]:
    """Collapse words that share a position (a handful of Barnes PDFs
    literally duplicate a row's text on top of itself, e.g. both "45.5" and
    the mangled "455" at the exact same coordinates) -- keeping whichever
    copy has a decimal point when the two disagree on that."""
    by_pos: dict[tuple[float, float], dict] = {}
    for w in row:
        key = (round(w["top"], 1), round(w["x0"], 1))
        existing = by_pos.get(key)
        if existing is None or ("." in w["text"] and "." not in existing["text"]):
            by_pos[key] = w
    return list(by_pos.values())


def _parse_table_from_words(
    words: list[dict], columns: list[tuple[str, float]], top_lo: float, top_hi: float
) -> list[LoadLine]:
    """Parse the data rows for one section, bounded to (top_lo, top_hi)."""
    # A tight row tolerance is deliberate: a sidebar bullet-description box
    # (rifle-layout PDFs) sits at nearly the same height as some table rows,
    # and a loose tolerance merges the two into one garbled row.
    section_words = [w for w in words if top_lo < w["top"] < top_hi]
    rows = pdf_utils.cluster_rows(section_words, tol=1.2)
    loads = []
    for row in rows:
        row = _dedupe_by_position(row)
        # A word only counts as a table value if it's actually a number
        # near one of the 4 known column positions -- this is what keeps
        # sidebar/footer text out, regardless of stray vertical overlap.
        numeric = []
        for w in row:
            val, suffix = _parse_number(w["text"])
            if val is None:
                continue
            col, col_x = min(columns, key=lambda c: abs(c[1] - w["x0"]))
            if abs(col_x - w["x0"]) <= _COL_TOLERANCE:
                numeric.append((w, val, suffix, col))
        if not numeric:
            continue
        min_num_x0 = min(w["x0"] for w, *_ in numeric)
        name_tokens = [w["text"] for w in row if w["x0"] < min_num_x0 - 2]
        if not name_tokens:
            continue
        name = " ".join(t for t in name_tokens if not re.match(r"^[A-Za-z0-9]$", t))
        is_max_marker = name.startswith("*")
        name = name.lstrip("*").strip()
        values = {}
        flags = []
        for w, val, suffix, col in numeric:
            values[col] = val
            if "c" in suffix.lower():
                flags.append("compressed")
        # Real Barnes rows always populate at least 3 of the 4 columns; a
        # stray meta-text number (e.g. a bullet weight in "125") landing
        # within tolerance of one column looks like a row otherwise.
        if len(values) < 3:
            continue
        # A max load is always a higher charge at a higher velocity than the
        # corresponding min/start load -- true for every real row regardless
        # of cartridge size (unlike a fixed magnitude cutoff, which can't
        # tell a legitimate 250gr charge in a .50 BMG from a garbled one).
        cmin, cmax = values.get("charge_min"), values.get("charge_max")
        vmin, vmax = values.get("velocity_min"), values.get("velocity_max")
        if cmin is not None and cmax is not None and cmax <= cmin:
            continue
        if vmin is not None and vmax is not None and vmax <= vmin:
            continue
        loads.append(
            LoadLine(
                powder=name,
                charge_min_gr=values.get("charge_min"),
                velocity_min_fps=values.get("velocity_min"),
                charge_max_gr=values.get("charge_max"),
                velocity_fps=values.get("velocity_max"),
                is_max=True,
                flags=", ".join((["most accurate"] if is_max_marker else []) + flags),
            )
        )
    return loads


_COAL_RE = re.compile(r"\bCOAL:?\s*([\d.]+)", re.I)
_CASE_RE = re.compile(r"\bCase:\s*(\S+)", re.I)
_PRIMER_RE = re.compile(r"Primer:\s*([A-Za-z0-9 .#-]+?)(?:\s{2,}|$)", re.I)
_BARREL_LEN_RE = re.compile(r"Barrel\s+Length:\s*([\d.\"]+)", re.I)
_TWIST_RE = re.compile(r"Twist\s+Rate:\s*(\S+)", re.I)
_RIFLE_TRIGGER_RE = re.compile(r"^(\d+)-?grain$", re.I)
_INFO_BLOCK_WEIGHT_RE = re.compile(r"^(\d+)gr\.?$", re.I)
_META_STOP_WORDS = {"Sectional", "Ballistic", "Density", "Coefficient", "C.O.A.L", "Suggested", "Bullet", "Use"}
_STRAY_TOKEN_RE = re.compile(r"^[a-z]$|^\.\d+$")


def _clean_bullet_type(raw: str) -> str:
    """Drop stray fragments that leak in from adjacent page furniture: single
    letters from a rotated caliber label, and bare ".NNN" SD/BC values whose
    "Sectional Density"/"Ballistic Coefficient" label was already filtered."""
    words = [w for w in raw.split() if not _STRAY_TOKEN_RE.match(w)]
    return " ".join(words).strip(" /")

PAGE_OFFSET = 2000.0  # > any real page height; keeps top-to-bottom order across pages


def _flatten_pages(pages: list[list[dict]]) -> list[dict]:
    flat = []
    for page_index, words in enumerate(pages):
        for w in words:
            w2 = dict(w)
            w2["top"] = w["top"] + page_index * PAGE_OFFSET
            flat.append(w2)
    return flat


def _find_all_triggers(words: list[dict]) -> list[tuple[float, float | None, str]]:
    """Find every "new bullet section starts here" marker, in document order.

    Returns (top, bullet_weight_gr, bullet_type) triples. Barnes uses two
    different triggers depending on PDF layout:
    - handgun style: a "Bullet Weight: N gr ... Bullet Style: X" line.
    - rifle style: a sidebar box starting "N-grain <type>".
    """
    triggers = []

    weight_words = sorted((w for w in words if w["text"] == "Weight:"), key=lambda w: w["top"])
    for ww in weight_words:
        preceding_bullet = any(
            w["text"] == "Bullet" and abs(w["top"] - ww["top"]) < 3 and w["x0"] < ww["x0"]
            for w in words
        )
        if not preceding_bullet:
            continue
        same_row = sorted(
            (w for w in words if abs(w["top"] - ww["top"]) < 3 and w["x0"] > ww["x0"]),
            key=lambda w: w["x0"],
        )
        weight = None
        if same_row and (m := re.match(r"([\d.]+)", same_row[0]["text"])):
            weight = float(m.group(1))
        style = ""
        style_words = [w for w in words if w["text"] == "Style:" and 0 < w["top"] - ww["top"] < 20]
        if style_words:
            sw = style_words[0]
            style_row = sorted(
                (w for w in words if abs(w["top"] - sw["top"]) < 3 and w["x0"] > sw["x0"]),
                key=lambda w: w["x0"],
            )
            if style_row:
                style = style_row[0]["text"]
        triggers.append((ww["top"], weight, style))

    # The rifle sidebar box is a narrow left-margin column. Bounding it on
    # both sides keeps out the main table (x0 >= ~160) and a vertical
    # rotated caliber label running down the page's far-left edge.
    sidebar_min_x0 = 30.0
    sidebar_max_x1 = 160.0

    def _in_sidebar(x: dict) -> bool:
        return sidebar_min_x0 <= x["x0"] and x["x1"] <= sidebar_max_x1

    for w in words:
        m = _RIFLE_TRIGGER_RE.match(w["text"])
        if not m or not _in_sidebar(w):
            continue
        rest = sorted(
            (
                x
                for x in words
                if abs(x["top"] - w["top"]) < 3
                and w["x0"] < x["x0"]
                and _in_sidebar(x)
                and x["text"] not in _META_STOP_WORDS
            ),
            key=lambda x: x["x0"],
        )
        # a rifle sidebar box wraps onto a second line right below the first
        wrap = sorted(
            (
                x
                for x in words
                if 3 <= x["top"] - w["top"] < 14
                and _in_sidebar(x)
                and x["text"] not in _META_STOP_WORDS
                and not _RIFLE_TRIGGER_RE.match(x["text"])
            ),
            key=lambda x: x["x0"],
        )
        bullet_type = _clean_bullet_type(" ".join(x["text"] for x in rest + wrap))
        triggers.append((w["top"], float(m.group(1)), bullet_type))

    # Newer rifle PDFs open each page with a "Bullet Weight/Type/SKU/S.D/G1
    # B.C/Cartridge O.A.L" reference table listing every bullet available in
    # that caliber, immediately above the page's own powder table -- e.g. a
    # row like "69gr. | Match Burner BT | 30162 | 0.196 | 0.339 | 2.410"".
    # Its numbers (SKU/SD/BC) sit in their own columns, unrelated to the
    # charge/velocity ones below, but need recognizing here or they get
    # treated as an untriggered, unlabeled section by the pairing logic.
    for sku in (w for w in words if w["text"] == "SKU"):
        same_row = [w for w in words if abs(w["top"] - sku["top"]) < 3]
        weight_hdr = next((w for w in same_row if w["text"] == "Weight"), None)
        type_hdr = next((w for w in same_row if w["text"] == "Type"), None)
        if weight_hdr is None:
            continue
        weight_rows = sorted(
            (
                w
                for w in words
                if sku["top"] < w["top"] < sku["top"] + 150
                and _INFO_BLOCK_WEIGHT_RE.match(w["text"])
                and abs(w["x0"] - weight_hdr["x0"]) < 20
            ),
            key=lambda w: w["top"],
        )
        for wr in weight_rows:
            weight = float(_INFO_BLOCK_WEIGHT_RE.match(wr["text"]).group(1))
            bullet_type = ""
            if type_hdr is not None:
                type_words = sorted(
                    (
                        x
                        for x in words
                        if abs(x["top"] - wr["top"]) < 10
                        and type_hdr["x0"] - 10 <= x["x0"] < type_hdr["x0"] + 80
                    ),
                    key=lambda x: (x["top"], x["x0"]),
                )
                bullet_type = " ".join(x["text"] for x in type_words)
            triggers.append((wr["top"], weight, bullet_type))

    triggers.sort(key=lambda t: t[0])
    return triggers


def _find_section_boundaries(words: list[dict]) -> list[float]:
    """Extra hard stops a table's data range must never cross, beyond the
    next header -- currently just the "SKU" bullet-reference block's own
    header row (see `_find_all_triggers`), whose SKU/SD/BC numbers can
    otherwise land within tolerance of a *different* section's charge or
    velocity column purely by coincidence."""
    return sorted(w["top"] for w in words if w["text"] == "SKU")


def _pair_headers_with_triggers(
    headers: list[tuple[list[tuple[str, float]], float]],
    triggers: list[tuple[float, float | None, str]],
) -> list[tuple[float, float | None, str] | None]:
    """Match each header to the trigger (bullet-weight/sidebar marker) it belongs to.

    Normally a trigger sits just *above* its header (the sidebar box or
    "Bullet Weight:" line is drawn before the table). The one exception is
    the very first block on a page, where the header is pinned at the page's
    top margin and its trigger is drawn just *below* it instead. A page can
    also hold a trailing, unmatched trigger whose table continues onto the
    next page -- left unassigned here, it gets picked up once that page's
    header is reached.
    """
    used = [False] * len(triggers)
    pairs: list[tuple[float, float | None, str] | None] = []
    for i, (_, header_top) in enumerate(headers):
        next_header_top = headers[i + 1][1] if i + 1 < len(headers) else float("inf")
        best = None
        for j, (trig_top, _, _) in enumerate(triggers):
            if used[j] or trig_top >= header_top:
                continue
            if best is None or trig_top > triggers[best][0]:
                best = j
        if best is None:
            for j, (trig_top, _, _) in enumerate(triggers):
                if used[j] or not (header_top <= trig_top < next_header_top):
                    continue
                if best is None or trig_top < triggers[best][0]:
                    best = j
        if best is not None:
            used[best] = True
            pairs.append(triggers[best])
        else:
            pairs.append(None)
    return pairs


def _meta_from_text(text: str) -> dict:
    d = {}
    if m := _CASE_RE.search(text):
        d["case"] = m.group(1)
    if m := _PRIMER_RE.search(text):
        d["primer"] = m.group(1).strip()
    if m := _BARREL_LEN_RE.search(text):
        d["barrel"] = m.group(1)
    if m := _TWIST_RE.search(text):
        d["twist"] = m.group(1)
    if m := _COAL_RE.search(text):
        d["coal_in"] = m.group(1)
    return d


def _parse_pdf(pdf_bytes: bytes, cartridge_label: str, source_url: str) -> list[LoadTable]:
    pages = pdf_utils.words_by_page(pdf_bytes)
    words = _flatten_pages(pages)
    tables: list[LoadTable] = []

    # Document-wide meta (case/primer/barrel/twist) as a fallback for
    # whichever fields a given section doesn't repeat locally.
    full_text = pdf_utils.pdftotext_layout(pdf_bytes)
    global_meta = _meta_from_text(full_text)

    headers = _find_all_headers(words)
    triggers = _find_all_triggers(words)
    paired = _pair_headers_with_triggers(headers, triggers)
    boundaries = _find_section_boundaries(words)

    for i, (columns, header_top) in enumerate(headers):
        next_header_top = headers[i + 1][1] if i + 1 < len(headers) else float("inf")
        if paired[i] is not None:
            trig_top, bullet_weight, bullet_type = paired[i]
        else:
            trig_top, bullet_weight, bullet_type = header_top, None, None
        next_boundary = next((b for b in boundaries if b > header_top), float("inf"))
        top_hi = min(next_header_top, next_boundary)

        section_lo = min(trig_top, header_top) - 5
        local_words = [w for w in words if section_lo < w["top"] < top_hi]
        local_text = " ".join(w["text"] for w in local_words)
        meta = {**global_meta, **_meta_from_text(local_text)}

        loads = _parse_table_from_words(words, columns, header_top + 5, top_hi)
        if not loads:
            continue

        tables.append(
            LoadTable(
                manufacturer=NAME,
                cartridge=cartridge_label,
                bullet_weight_gr=bullet_weight,
                bullet_type=bullet_type or "",
                coal_in=meta.get("coal_in"),
                case=meta.get("case", ""),
                primer=meta.get("primer", ""),
                barrel=meta.get("barrel", ""),
                twist=meta.get("twist", ""),
                loads=loads,
                source_url=source_url,
            )
        )
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

    try:
        pdf_bytes = pdf_utils.fetch_pdf(entry["url"], s)
        result.tables = _parse_pdf(pdf_bytes, label, entry["url"])
        if not result.tables:
            result.errors.append("PDF fetched but no load tables could be parsed from it")
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"failed to fetch/parse {entry['url']}: {exc}")
    return result
