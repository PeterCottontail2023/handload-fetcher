"""Common data model that every manufacturer source module returns.

Every site presents load data a little differently (some give a single
"max charge", some give a starting/max pair, some give charge-at-velocity
tables), so `LoadLine` keeps the optional fields loose rather than forcing
one shape. Fields that a given source can't supply are left as None.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LoadLine:
    """One powder's data point (or range) for a given bullet."""

    powder: str
    charge_gr: Optional[float] = None       # single-point charge (e.g. "the max load")
    charge_min_gr: Optional[float] = None   # starting/min charge, if given as a range
    charge_max_gr: Optional[float] = None   # max charge, if given as a range
    velocity_fps: Optional[float] = None       # velocity for charge_gr / charge_max_gr
    velocity_min_fps: Optional[float] = None   # velocity for charge_min_gr
    is_max: bool = False
    flags: str = ""   # short free-text markers from the source, e.g. "most accurate", "compressed"

    def as_row(self) -> dict:
        return {
            "powder": self.powder,
            "charge_gr": self.charge_gr,
            "charge_min_gr": self.charge_min_gr,
            "charge_max_gr": self.charge_max_gr,
            "velocity_fps": self.velocity_fps,
            "velocity_min_fps": self.velocity_min_fps,
            "is_max": self.is_max,
            "flags": self.flags,
        }

    def charge_display(self) -> str:
        """Human-readable charge weight, shared by the terminal and HTML report."""
        if self.charge_min_gr is not None and self.charge_max_gr is not None:
            return f"{self.charge_min_gr:g}–{self.charge_max_gr:g} gr"
        if self.charge_gr is not None:
            return f"{self.charge_gr:g} gr" + (" (MAX)" if self.is_max else "")
        if self.charge_min_gr is not None:
            return f"{self.charge_min_gr:g} gr (start)"
        return "—"

    def velocity_display(self) -> str:
        if self.velocity_min_fps is not None and self.velocity_fps is not None:
            return f"{self.velocity_min_fps:g}–{self.velocity_fps:g} fps"
        if self.velocity_fps is not None:
            return f"{self.velocity_fps:g} fps"
        return "—"


@dataclass
class LoadTable:
    """All the load lines that share one bullet / test setup."""

    manufacturer: str
    cartridge: str
    bullet_weight_gr: Optional[float] = None
    bullet_type: str = ""
    bullet_id: str = ""          # manufacturer part/SKU, if given
    coal_in: Optional[str] = None
    case: str = ""
    primer: str = ""
    barrel: str = ""
    twist: str = ""
    test_firearm: str = ""
    loads: list[LoadLine] = field(default_factory=list)
    source_url: str = ""
    notes: str = ""

    def __str__(self) -> str:
        head = f"{self.manufacturer} — {self.cartridge}"
        if self.bullet_weight_gr:
            head += f", {self.bullet_weight_gr:g}gr"
        if self.bullet_type:
            head += f" {self.bullet_type}"
        lines = [head]
        if self.source_url:
            lines.append(f"  source: {self.source_url}")
        for ld in self.loads:
            bits = [f"    {ld.powder:<20}", ld.charge_display(), ld.velocity_display()]
            if ld.flags:
                bits.append(f"[{ld.flags}]")
            lines.append("  ".join(bits))
        return "\n".join(lines)


@dataclass
class SourceResult:
    """What a source module hands back for one query."""

    manufacturer: str
    query: str
    matched_cartridge: Optional[str] = None
    tables: list[LoadTable] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.tables) and not self.errors
