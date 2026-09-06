# Handload Fetcher

_by [PeterCottontail2023](https://github.com/PeterCottontail2023)_

Pulls published reloading load data from manufacturer sites for a given
cartridge and normalizes it into one common table shape (powder, charge
weight, velocity, plus whatever bullet/case/primer/barrel context that
site publishes).

**Not comfortable with a terminal?** Run `run-windows.bat` (Windows),
`run-mac.command` (Mac), or `run-linux.sh` (Linux) in this folder instead
of anything below -- they handle first-time setup themselves and just ask
you for a cartridge name. Double-click works for the Windows and Mac ones;
on Mac the first double-click may need a right-click -> Open to clear a
Gatekeeper warning (macOS quarantining any downloaded script, nothing
specific to this one). On Linux, double-click behavior depends on your
file manager -- if it doesn't just run, right-click -> Run (or Run in
Terminal), or open a terminal here and run `./run-linux.sh`.

## Sources implemented

| Source | What's actually there | How it's fetched |
|---|---|---|
| Nosler | one page per cartridge, a small text-based PDF per bullet weight | HTML listing -> cartridge page -> PDF, `pdftotext` |
| Sierra | one PDF per cartridge (charge-at-velocity grid, page per bullet) | HTML accordion listing -> PDF, `pdfplumber` word positions |
| Barnes | one PDF per cartridge (handgun: min/max charge; rifle: same, plus a bullet sidebar) | HTML accordion listing -> PDF, `pdfplumber` word positions |
| Speer | two overlapping catalogs on the Handgun/Rifle listing pages -- a JSON blob behind the interactive picker, plus a much larger static link list the picker doesn't know about -- and two PDF templates (old/new) | JSON + HTML in page -> PDF, `pdfplumber` word positions |
| Hammer Bullets | a Google Sheet per cartridge, linked off the (age-gated) load-data page | age-gate cookie -> HTML listing -> Sheet CSV export |
| Hodgdon | **not yet implemented** -- served behind Cloudflare/JS rendering | -- |

None of these expose a real API; each module reverse-engineers that one
site's actual page/PDF/spreadsheet structure. See the docstring at the top
of each `sources/*.py` file for the specifics and the assumptions baked in.

## Setup

Run this from the repo root (the directory this README is in, containing
`requirements.txt` and the `handloadfetcher/` package folder).

`requests` and `beautifulsoup4` are common enough that they're likely
already on your system Python; `pdfplumber` usually isn't, and Debian/Ubuntu
Python now refuses a plain `pip install` for it ("externally-managed-
environment"). Rather than a venv (not always available on every system),
its dependencies are vendored into `handloadfetcher/vendor`, picked up
automatically by `handloadfetcher/__init__.py`:

```
pip install --target=handloadfetcher/vendor -r requirements.txt
```

(The launcher scripts below do this for you automatically the first time
you run them -- this is only for a manual/from-source setup.)

(`--break-system-packages` instead works too, if you'd rather install it
properly into your system Python.)

`pdfplumber` itself needs no system dependencies. `pdf_utils.pdftotext_layout`
additionally shells out to poppler's `pdftotext` CLI (`apt install
poppler-utils` / `brew install poppler`) for the sources whose PDFs are
plain left-to-right text (Nosler, and the shared meta fields on Barnes and
Speer); the rest of the PDF parsing uses `pdfplumber` directly.

## Usage

Run it with no arguments for an interactive prompt -- it just asks for a
cartridge, repeatedly, until you hit Enter on a blank line:

```
python3 -m handloadfetcher
```

Or one-shot, e.g. for scripting:

```
python3 -m handloadfetcher "9mm luger"
python3 -m handloadfetcher ".308 win" --sources barnes,sierra
python3 -m handloadfetcher "9mm luger" --no-report      # terminal output only
python3 -m handloadfetcher "9mm luger" --no-open        # write the report, don't open it
```

Every run prints to the terminal *and* writes a self-contained HTML report
(`handloadfetcher/reports/<cartridge>-<timestamp>.html`, gitignored) grouped
by manufacturer then bullet, which opens automatically in your default
browser (`report.py`; `--no-report` / `--no-open` / `--report-dir` control
that). It has no external dependencies (no CDN, works offline) and is
print-friendly if you want a bench copy. Both the terminal output and the
report are tagged "Handload Fetcher — by PeterCottontail2023".

Or from code:

```python
from handloadfetcher.cli import get_load_data
from handloadfetcher import report

results = get_load_data("9mm luger")   # one models.SourceResult per source
for r in results:
    for table in r.tables:
        print(table)                   # manufacturer, bullet, and its load lines

path = report.write_report("9mm luger", results)
report.open_in_browser(path)
```

Cartridge names are matched fuzzily against each site's own listing
(`cartridges.py`) -- abbreviations (`win`→Winchester, `s&w`→Smith & Wesson,
`mag`→Magnum, ...) are expanded, the Unicode "×" is treated the same as an
ASCII "x" (7×57 / 7x57), word order doesn't matter, but a numeric
designator you type (e.g. "223") must match one in the candidate exactly,
so it can never silently return a different cartridge's data (e.g. "222").
A source that doesn't carry a cartridge reports "no matching cartridge
found" rather than guessing.

## Known rough edges

- Bullet-variant descriptions (the free-text "type" on a table, e.g. "TSX
  BT / TAC-TX BT") are pulled from PDF layout heuristics and occasionally
  pick up a stray character or mislabel which of several same-weight
  bullets a table belongs to. The charge/velocity numbers themselves are
  matched to their column independently of that and have been spot-checked
  against the source PDFs directly.
- Speer's two catalogs (JSON + legacy static list) aren't de-duplicated
  against each other, so a cartridge covered by both can show the same
  bullet weight twice, sourced from two different PDF templates.
- Hammer Bullets' sheet fetch (`docs.google.com/.../export?format=csv`)
  couldn't be exercised end-to-end from this sandbox (no network access to
  Google); the age-gate/listing-page half is verified live, and the CSV
  parser is unit-tested against a real sheet's exported content, but treat
  the very first live run there as the thing to double check.
- A listing page is cached for a week (`handloadfetcher/.cache/`) once
  fetched; delete that directory to force a re-scrape sooner.

## Adding Hodgdon

hodgdonreloading.com's load data is rendered client-side (Cloudflare in
front, page ships mostly empty HTML) -- the sites above are the "easy"
ones for exactly that reason. Making that one work will need either
finding the underlying data API/XHR calls the page's JS makes, or driving
a real/headless browser instead of a plain HTTP fetch. Not started yet.
