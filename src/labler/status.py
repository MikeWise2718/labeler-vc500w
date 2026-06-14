"""Parse the VC-500W status.xml reply into a Status dataclass.

The printer replies to a status query with two concatenated XML documents: a small
envelope (`<status><code>..</code><datasize>..</datasize></status>`) followed by the
real status body. We regex the fields we care about out of the combined text — the
firmware's XML is simple and flat, so this is robust and avoids namespace fuss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def _find(text: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else None


def _find_int(text: str, tag: str) -> int | None:
    v = _find(text, tag)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _find_float(text: str, tag: str) -> float | None:
    v = _find(text, tag)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


@dataclass
class Status:
    """Parsed printer status."""

    print_state: str | None = None        # IDLE | BUSY | ...
    print_job_stage: str | None = None     # READY FOR PRINT | PRINTING | PREHEAT | ...
    print_job_error: str | None = None     # NONE | ...
    remain: float | None = None            # tape remaining (units per firmware)
    cassette_type: int | None = None       # 1 = 25 mm (CZ-1004), others TBD
    online: bool | None = None
    capacity: int | None = None            # power/battery %
    raw: str = ""

    # Idle stages that mean "not mid-job" — ready to accept work. "SUCCESS" is the
    # terminal stage left after a completed print; "READY FOR PRINT" is the cold state.
    _IDLE_STAGES = ("READY", "SUCCESS", "IDLE")

    @property
    def ready(self) -> bool:
        """True when the printer is idle, error-free, and ready to accept a job."""
        if self.print_state != "IDLE":
            return False
        if self.print_job_error not in (None, "NONE"):
            return False
        stage = self.print_job_stage
        return stage is None or any(k in stage for k in self._IDLE_STAGES)

    @classmethod
    def parse(cls, text: str) -> "Status":
        online = _find_int(text, "online")
        return cls(
            print_state=_find(text, "print_state"),
            print_job_stage=_find(text, "print_job_stage"),
            print_job_error=_find(text, "print_job_error"),
            remain=_find_float(text, "remain"),
            cassette_type=_find_int(text, "cassette_type"),
            online=(bool(online) if online is not None else None),
            capacity=_find_int(text, "capacity"),
            raw=text,
        )
