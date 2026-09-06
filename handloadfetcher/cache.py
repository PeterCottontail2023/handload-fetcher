"""A tiny disk cache for manufacturer cartridge-listing pages.

Only the "what cartridges/URLs does this site have" listing is cached --
never the actual load numbers, which are always fetched fresh. This just
avoids re-parsing a whole load-data index page (or, for Hammer, re-doing
the age-gate dance) on every single query.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent / ".cache"
DEFAULT_MAX_AGE = 7 * 24 * 3600  # a week; these listings change rarely


def load(name: str, max_age: float = DEFAULT_MAX_AGE):
    path = CACHE_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - payload.get("_saved_at", 0) > max_age:
        return None
    return payload.get("data")


def save(name: str, data) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.json"
    path.write_text(json.dumps({"_saved_at": time.time(), "data": data}))
