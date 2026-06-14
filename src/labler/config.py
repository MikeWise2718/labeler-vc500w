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
# raw IP is the fallback if mDNS resolution is unavailable.
DEFAULT_HOST = "VC-500W5087.local"
FALLBACK_HOST = "192.168.25.219"
PORT = 9100


@dataclass(frozen=True)
class Media:
    """A tape/cassette type."""

    name: str            # Brother cassette part number
    width_mm: float      # physical tape width
    cassette_type: int | None = None  # value reported in status.xml, if known

    @property
    def width_px(self) -> int:
        """Across-tape printable width in pixels."""
        return round(self.width_mm * PX_PER_MM)


# Known media. cassette_type=1 confirmed for 25 mm (CZ-1004); others TBD.
MEDIA: dict[int, Media] = {
    9:  Media("CZ-1003", 9),
    12: Media("CZ-1002", 12),
    19: Media("CZ-1001", 19),
    25: Media("CZ-1004", 25, cassette_type=1),
    50: Media("CZ-1005", 50),
}

# Phase 1 supports these widths via the CLI; the rest are table entries for later.
SUPPORTED_WIDTHS = (25, 50)


def media_for(width_mm: int) -> Media:
    """Look up a Media by integer width (mm), or raise ValueError."""
    try:
        return MEDIA[width_mm]
    except KeyError:
        widths = ", ".join(str(w) for w in sorted(MEDIA))
        raise ValueError(f"unknown media width {width_mm} mm (known: {widths})")


CONFIG_DIR = Path.home() / ".config" / "labler"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Settings:
    """User-overridable defaults, persisted to ~/.config/labler/config.json."""

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
