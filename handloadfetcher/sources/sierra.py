"""Sierra Bullets (sierrabullets.com/load-data/).

The load-data page lists a PDF per cartridge, grouped into accordions by
caliber. Each PDF has an intro/specs page followed by one page per bullet
(or small group of bullets sharing one table), formatted as a sparse
charge-at-velocity grid: a row of velocity checkpoints across the top, and
each powder lists only the charge weights that hit some subset of them --
so charge values are matched to a velocity by x-position, not by count.

A large diagonal "this data is for individual use only" watermark is drawn
across the intro page; pdfplumber reports it with negative x-coordinates
(an artifact of its rotation), which makes it trivial to filter out.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .. import cartridges, pdf_utils
from ..http import new_session
from ..models import LoadLine, LoadTable, SourceResult

NAME = "Sierra"
LOAD_DATA_URL = "https://sierrabullets.com/load-data/"
CACHE_KEY = "sierra_cartridges"


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
        if href.lower().endswith(".pdf") and "/load-data/" in href.lower():
            label = a.get_text(strip=True)
            if not label:
                continue
            url = href if href.startswith("http") else f"https://sierrabullets.com{href}"
            items.append({"label": label, "url": url})

    cache.save(CACHE_KEY, items)
    return items


def _real_words(page_words: list[dict]) -> list[dict]:
    """Drop the diagonal watermark, which pdfplumber reports at negative x0."""
    return [w for w in page_words if w["x0"] >= 0]


_LABEL_RE = {
    "case": re.compile(r"^Case:$"),
    "primer": re.compile(r"^Primer:$"),
    "barrel": re.compile(r"^Length:$"),  # follows "Barrel"
    "twist": re.compile(r"^Twist:$"),
    "test_firearm": re.compile(r"^Used:$"),  # follows "Firearm"
}


def _value_after(words: list[dict], label_word: dict, stop_at: set[str] | None = None) -> str:
    same_row = sorted(
        (w for w in words if abs(w["top"] - label_word["top"]) < 3 and w["x0"] > label_word["x0"]),
        key=lambda w: w["x0"],
    )
    out = []
    for w in same_row:
        if stop_at and w["text"] in stop_at:
            break
        out.append(w["text"])
    return " ".join(out)


def _parse_specs(page0_words: list[dict]) -> dict:
    words = _real_words(page0_words)
    meta = {}
    for w in words:
        if w["text"] == "Case:":
            meta["case"] = _value_after(words, w)
        elif w["text"] == "Primer:":
            meta["primer"] = _value_after(words, w)
        elif w["text"] == "Twist:":
            meta["twist"] = _value_after(words, w)
        elif w["text"] == "Barrel" :
            nxt = [x for x in words if x["text"] == "Length:" and abs(x["top"] - w["top"]) < 3]
            if nxt:
                meta["barrel"] = _value_after(words, nxt[0])
        elif w["text"] == "Firearm":
            nxt = [x for x in words if x["text"] == "Used:" and abs(x["top"] - w["top"]) < 3]
            if nxt:
                meta["test_firearm"] = _value_after(words, nxt[0])
    return meta


_BULLET_ROW_RE = re.compile(r"^#?\S+$")


def _parse_bullet_variants(words: list[dict]) -> str:
    """Summarize the "Bullet / Caliber / Weight / Type / C.O.A.L." rows
    that head each data page into a short human-readable description."""
    header_words = {w["text"]: w for w in words if w["text"] in ("Bullet", "Caliber", "Weight", "Type")}
    if "Bullet" not in header_words:
        return ""
    header_top = header_words["Bullet"]["top"]
    powder_hdr = min(
        (w for w in words if w["text"] == "Powder"), key=lambda w: w["top"], default=None
    )
    cutoff = powder_hdr["top"] if powder_hdr else header_top + 200
    rows = pdf_utils.cluster_rows(
        [w for w in words if header_top + 5 < w["top"] < cutoff], tol=1.5
    )
    descriptions = []
    for row in rows:
        texts = [w["text"] for w in row]
        # weight looks like "90" + "gr", or a single merged token "110gr."
        weight = ""
        gr_index = None
        for i, t in enumerate(texts):
            if re.match(r"^\d+gr\.?$", t):
                weight = t.rstrip(".")
                gr_index = i
                break
            if re.match(r"^\d+$", t) and i + 1 < len(texts) and texts[i + 1] == "gr":
                weight = f"{t}gr"
                gr_index = i + 1
                break
        # bullet type: words after the weight up to the trailing COAL measurement
        type_words = []
        if gr_index is not None:
            for t in texts[gr_index + 1 :]:
                if not re.match(r'^[\d.]+"$', t):
                    type_words.append(t)
        desc = " ".join(filter(None, [weight, " ".join(type_words)])).strip()
        if desc:
            descriptions.append(desc)
    # dedupe while preserving order
    seen = set()
    unique = [d for d in descriptions if not (d in seen or seen.add(d))]
    return " / ".join(unique)


_NUM_RE = re.compile(r"^\*{0,2}(\d+(?:\.\d+)?)\*{0,2}$")


def _parse_load_page(words: list[dict]) -> tuple[str, list[LoadLine]]:
    words = _real_words(words)
    bullet_desc = _parse_bullet_variants(words)

    # A rifle page can carry a second "Powder"/"Velocity" pair further down
    # in its own "Special Load" (accuracy/hunting pick) mini-table -- take
    # the topmost pair, since the main table always precedes it, and
    # `extract_words` does not promise top-to-bottom iteration order.
    powder_hdr = min(
        (w for w in words if w["text"] == "Powder"), key=lambda w: w["top"], default=None
    )
    velocity_hdr = next(
        (w for w in words if w["text"] == "Velocity" and powder_hdr and abs(w["top"] - powder_hdr["top"]) < 3),
        None,
    )
    if powder_hdr is None or velocity_hdr is None:
        return bullet_desc, []

    header_top = powder_hdr["top"]
    vel_columns = sorted(
        (
            (float(w["text"]), w["x0"])
            for w in words
            if abs(w["top"] - header_top) < 3 and re.match(r"^\d+$", w["text"]) and w["x0"] > velocity_hdr["x0"]
        ),
        key=lambda c: c[1],
    )
    if not vel_columns:
        return bullet_desc, []

    energy_hdr = min(
        (w for w in words if w["text"] == "Energy" and w["top"] > header_top),
        key=lambda w: w["top"],
        default=None,
    )
    cutoff = energy_hdr["top"] if energy_hdr else header_top + 300

    rows = pdf_utils.cluster_rows(
        [w for w in words if header_top + 5 < w["top"] < cutoff], tol=3.0
    )
    loads: list[LoadLine] = []
    for row in rows:
        numeric = []
        numeric_words = []
        for w in row:
            m = _NUM_RE.match(w["text"])
            if not m:
                continue
            val = float(m.group(1))
            col_val, col_x = min(vel_columns, key=lambda c: abs(c[1] - w["x0"]))
            # Only a value actually near a velocity column counts as data --
            # this is also what tells a real number apart from a powder name
            # that happens to split off its digits as a separate word, e.g.
            # "H" + "4895" for H4895 (rifle-page fonts do this routinely).
            if abs(col_x - w["x0"]) <= 12:
                numeric.append((val, col_val, "MAX" in w["text"] or "*" in w["text"]))
                numeric_words.append(w)
        if not numeric:
            continue
        min_num_x0 = min(w["x0"] for w in numeric_words)
        name = " ".join(w["text"] for w in row if w["x0"] < min_num_x0 - 2)
        if not name:
            continue
        # the highest-velocity point tested is the max load for that powder
        numeric.sort(key=lambda t: t[1])
        for idx, (charge, vel, is_max_flag) in enumerate(numeric):
            loads.append(
                LoadLine(
                    powder=name,
                    charge_gr=charge,
                    velocity_fps=vel,
                    is_max=(idx == len(numeric) - 1),
                )
            )
    return bullet_desc, loads


def _parse_pdf(pdf_bytes: bytes, cartridge_label: str, source_url: str) -> list[LoadTable]:
    pages = pdf_utils.words_by_page(pdf_bytes)
    if not pages:
        return []
    specs = _parse_specs(pages[0]) if pages else {}

    tables = []
    for page_words in pages[1:]:
        if not any(w["text"] == "Powder" for w in page_words):
            continue
        bullet_desc, loads = _parse_load_page(page_words)
        if not loads:
            continue
        tables.append(
            LoadTable(
                manufacturer=NAME,
                cartridge=cartridge_label,
                bullet_type=bullet_desc,
                case=specs.get("case", ""),
                primer=specs.get("primer", ""),
                barrel=specs.get("barrel", ""),
                twist=specs.get("twist", ""),
                test_firearm=specs.get("test_firearm", ""),
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
