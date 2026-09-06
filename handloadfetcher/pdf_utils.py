"""Shared PDF-fetching and text-extraction helpers.

Every manufacturer except Hammer Bullets hands out load data as PDFs. Two
extraction strategies are used depending on how the PDF was built:

- `pdftotext_layout` (via the poppler-utils CLI) for PDFs where the load
  table is plain left-to-right text -- Nosler and Barnes.
- `words_by_page` (via pdfplumber, coordinate-based) for PDFs where the
  table has no real cell grid and values are position-matched to a header
  row -- Sierra.
"""
from __future__ import annotations

import io
import subprocess

import pdfplumber

from .http import new_session


def fetch_pdf(url: str, session=None, timeout: int = 30) -> bytes:
    s = session or new_session()
    resp = s.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def pdftotext_layout(pdf_bytes: bytes) -> str:
    """Run poppler's `pdftotext -layout` over in-memory PDF bytes.

    Requires the `pdftotext` binary (poppler-utils) on PATH.
    """
    proc = subprocess.run(
        ["pdftotext", "-layout", "-", "-"],
        input=pdf_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.decode("utf-8", errors="replace")


def words_by_page(pdf_bytes: bytes) -> list[list[dict]]:
    """Return, per page, the list of pdfplumber word dicts (text/x0/top/...)."""
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_words(use_text_flow=True, keep_blank_chars=False))
    return pages


def cluster_rows(words: list[dict], tol: float = 3.0) -> list[list[dict]]:
    """Group words into visual rows by their 'top' (y) position.

    Several of these manufacturers' load tables have no real cell grid --
    the only thing tying a powder name to its charge/velocity figures is
    that they were drawn at (about) the same height on the page.
    """
    rows: list[list[dict]] = []
    row_tops: list[float] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        for i, top in enumerate(row_tops):
            if abs(w["top"] - top) <= tol:
                rows[i].append(w)
                break
        else:
            rows.append([w])
            row_tops.append(w["top"])
    # re-sort each row left-to-right and the rows themselves top-to-bottom
    order = sorted(range(len(rows)), key=lambda i: row_tops[i])
    return [sorted(rows[i], key=lambda w: w["x0"]) for i in order]


def nearest_column(x0: float, columns: list[tuple[str, float]]) -> str:
    """Given [(column_name, reference_x0), ...], return the closest name."""
    return min(columns, key=lambda c: abs(c[1] - x0))[0]
