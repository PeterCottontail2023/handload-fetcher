"""Cartridge name normalization and fuzzy matching.

Users type cartridge names inconsistently ("9mm", "9mm luger", ".308",
"308 win", "308 winchester") and every manufacturer site spells them a bit
differently too. This module gives sources a common way to turn a messy
list of site labels into something matchable against a messy user query.
"""
from __future__ import annotations

import difflib
import re

# Common reloading-world abbreviations -> full word, applied as whole-token
# substitutions after the string is split on non-alphanumeric characters.
_ABBREVIATIONS = {
    "rem": "remington",
    "win": "winchester",
    "wby": "weatherby",
    "wsm": "winchester short magnum",
    "wssm": "winchester super short magnum",
    "rum": "remington ultra magnum",
    "ackley": "ackley improved",
    "ai": "ackley improved",
    "mag": "magnum",
    "spl": "special",
    "spc": "special",
    "cal": "caliber",
    "sw": "smith wesson",
    "s&w": "smith wesson",
    "gov": "government",
    "govt": "government",
    "auto": "automatic",
    "acp": "automatic colt pistol",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize(name: str) -> str:
    """Lowercase, drop punctuation, expand common abbreviations, sort words.

    Sorting words makes "Winchester 308" and "308 Winchester" compare equal,
    which matters more here than preserving natural word order.
    """
    # "&" is a word separator to _WORD_RE, so "S&W" would otherwise come out
    # as bare "s" + "w" and never hit the "sw" -> "smith wesson" expansion.
    collapsed = re.sub(r"(?i)\bs\s*&\s*w\b", "sw", name)
    # Some sites/sheets use the Unicode multiplication sign in "7×57" rather
    # than a plain "x" -- normalize it so both spellings tokenize the same
    # way ("7x57" as one token, not split into bare "7" and "57").
    collapsed = collapsed.replace("×", "x")
    tokens = _WORD_RE.findall(collapsed.lower())
    tokens = [_ABBREVIATIONS.get(t, t) for t in tokens]
    # Abbreviation expansion can itself produce multiple words; re-flatten.
    flat: list[str] = []
    for t in tokens:
        flat.extend(t.split())
    # A trailing "mm" glued onto a caliber/case designator is optional as
    # far as matching goes: users drop it ("9mm", "9.3x62") as often as
    # sites include it ("9MM", "9.3x62mm") -- and never consistently on
    # both sides of the same comparison otherwise.
    flat = [re.sub(r"(?<=\d)mm$", "", t) for t in flat]
    # Leading zeros / bare-caliber forms: "0.308" / "308" / "30" should all
    # compare the same for matching purposes as raw digit strings.
    flat = [t.lstrip("0") or "0" for t in flat]
    return " ".join(sorted(flat))


def _raw_tokens(name: str) -> list[str]:
    """Like normalize(), but keeps original word order (no sorting)."""
    tokens = _WORD_RE.findall(name.lower().replace("×", "x"))
    flat: list[str] = []
    for t in tokens:
        flat.extend(_ABBREVIATIONS.get(t, t).split())
    return [t.lstrip("0") or "0" for t in flat]


def score(query: str, candidate: str) -> float:
    """0..1 similarity between a user query and a site's cartridge label."""
    nq, nc = normalize(query), normalize(candidate)
    if nq == nc:
        return 1.0

    qnums = {t for t in nq.split() if t.isdigit()}
    cnums = {t for t in nc.split() if t.isdigit()}
    if qnums and not qnums <= cnums:
        # A numeric designator (caliber/case number) the user typed must
        # show up verbatim -- "223" and "222" must never fuzzy-match each
        # other just because the rest of the name reads similarly.
        return 0.0

    qset, cset = set(nq.split()), set(nc.split())
    if qset and qset <= cset:
        # Every word the user typed appears in the candidate -- that's
        # already a confident match (0.75 floor) regardless of how many
        # *extra* qualifying words the candidate's own label tacks on
        # (e.g. "7x57" fully matching inside "7mm Mauser (7x57mm)"), with
        # the remaining headroom rewarding a tighter, closer-length match.
        base = 0.75 + 0.25 * (len(qset) / max(len(cset), 1))
    else:
        # Plain character-similarity carries no guarantee that this is even
        # the same cartridge family (a coincidentally-similar-looking but
        # entirely different cartridge can still score respectably) -- so a
        # weak match here is treated as no match at all, rather than as this
        # source's best (wrong) guess.
        ratio = difflib.SequenceMatcher(None, nq, nc).ratio()
        base = ratio if ratio >= 0.75 else 0.0

    # Bonus for the candidate's own word order actually starting with what
    # the user typed -- distinguishes "223 Remington (AR-15)" from a wildcat
    # like "6x45mm (223 Rem Case)" that merely mentions the same numbers.
    # Capped just below 1.0: an exact match (returned above) must always
    # outrank a same-family variant like "223 Remington Handgun", even
    # though both would otherwise hit the same ceiling.
    q_raw, c_raw = _raw_tokens(query), _raw_tokens(candidate)
    if q_raw and c_raw[: len(q_raw)] == q_raw:
        base = min(0.95, base + 0.3)
    return base


def best_matches(query: str, candidates: list[str], limit: int = 5, min_score: float = 0.45):
    """Return up to `limit` (candidate, score) pairs, best first."""
    scored = sorted(
        ((c, score(query, c)) for c in candidates),
        key=lambda p: p[1],
        reverse=True,
    )
    return [p for p in scored[:limit] if p[1] >= min_score]
