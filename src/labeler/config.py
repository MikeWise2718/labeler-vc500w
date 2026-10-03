"""Configuration: printer host, media table, and persisted settings.

The media table converts physical tape width (mm) to the across-tape printable
width in pixels at the VC-500W's native resolution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# VC-500W native resolution: 313 DPI ~= 12.48 px/mm. Verified on 25 mm tape that
# the full ~312 px prints with no edge clipping (see CLAUDE.md / specs/design.md).
PX_PER_MM = 12.48

# Default printer on the home LAN. mDNS name is stable across DHCP leases; the
# raw IP is the fallback if mDNS resolution is unavailable. The IPv4 is a
# MAC-based DHCP reservation (04:FE:A1:53:BF:2B -> .190), so it survives the
# printer roaming between APs/SSIDs (e.g. PrettyFly -> Dungeon on the basement move).
DEFAULT_HOST = "VC-500W5087.local"
FALLBACK_HOST = "192.168.25.190"
PORT = 9100


@dataclass(frozen=True)
class Media:
    """A tape/cassette type."""

    name: str            # Brother cassette part number
    width_mm: float      # physical tape width
    cassette_type: str | None = None  # status.xml <cassette_type> = width in inches, if known

    @property
    def width_px(self) -> int:
        """Across-tape printable width in pixels."""
        return round(self.width_mm * PX_PER_MM)


# Known media. status.xml's <cassette_type> is the loaded tape's width in INCHES:
# "1" = 25 mm (CZ-1004, 2026-06-14), "2" = 50 mm (CZ-1005, 2026-09-26),
# "1/2" = 12 mm (CZ-1002, 2026-10-03). 9/19 mm are unconfirmed (likely "3/8"/"3/4")
# — we don't own those cassettes, so they stay unmapped rather than guessed.
MEDIA: dict[int, Media] = {
    9:  Media("CZ-1003", 9),
    12: Media("CZ-1002", 12, cassette_type="1/2"),
    19: Media("CZ-1001", 19),
    25: Media("CZ-1004", 25, cassette_type="1"),
    50: Media("CZ-1005", 50, cassette_type="2"),
}

# Phase 1 supports these widths via the CLI; the rest are table entries for later.
SUPPORTED_WIDTHS = (12, 25, 50)   # the cassettes we own


def media_for_cassette(cassette_type: str | int | None) -> Media | None:
    """The Media whose status.xml cassette_type matches, or None if unmapped.

    Accepts the raw text ("1/2") or an int (1) — compared as stripped text.
    """
    if cassette_type is None:
        return None
    key = str(cassette_type).strip()
    return next((m for m in MEDIA.values() if m.cassette_type == key), None)


def media_for(width_mm: int) -> Media:
    """Look up a Media by integer width (mm), or raise ValueError."""
    try:
        return MEDIA[width_mm]
    except KeyError:
        widths = ", ".join(str(w) for w in sorted(MEDIA))
        raise ValueError(f"unknown media width {width_mm} mm (known: {widths})")


CONFIG_DIR = Path.home() / ".config" / "labeler"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Settings:
    """User-overridable defaults, persisted to ~/.config/labeler/config.json."""

    host: str = DEFAULT_HOST
    media_width: int = 25
    mode: str = "vivid"        # vivid | normal
    cut: str = "full"          # none | half | full
    font: str | None = None    # path to a .ttf for print-text; None = default

    @classmethod
    def load(cls) -> "Settings":
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            valid = set(cls.__dataclass_fields__)
            return cls(**{k: v for k, v in data.items() if k in valid})
        return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")
