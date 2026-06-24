"""Runtime data layout + helpers for the web app.

All mutable state lives under ~/.labler/ (NOT in the code repo) per the workspace
code/runtime split rule:

    ~/.labler/
      settings.json        # server-side app settings
      logs/events.jsonl    # structured event log
      assets/              # uploaded source bitmaps (content-addressed)
      designs/<id>/        # saved display-lists + preview thumbnails
      history.jsonl        # append-only print log

The directory is created on first access. Settings here are the web app's
authority and are a superset of the CLI's ~/.config/labler/config.json.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import DEFAULT_HOST, FALLBACK_HOST  # noqa: F401  (re-exported for callers)

RUNTIME_DIR = Path.home() / ".labler"
SETTINGS_FILE = RUNTIME_DIR / "settings.json"
LOG_DIR = RUNTIME_DIR / "logs"
EVENTS_FILE = LOG_DIR / "events.jsonl"
ASSETS_DIR = RUNTIME_DIR / "assets"
DESIGNS_DIR = RUNTIME_DIR / "designs"
HISTORY_FILE = RUNTIME_DIR / "history.jsonl"
HISTORY_DIR = RUNTIME_DIR / "history"       # per-print thumbnails: <entry_id>.png


def ensure_runtime() -> None:
    """Create the runtime directory tree if missing. Cheap; safe to call often."""
    for d in (RUNTIME_DIR, LOG_DIR, ASSETS_DIR, DESIGNS_DIR, HISTORY_DIR):
        d.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WebSettings:
    """Server-side app settings, persisted to ~/.labler/settings.json.

    `host` defaults to the IPv4 address, NOT the mDNS name: this session the mDNS
    name resolved to IPv6 and refused :9100. The IPv4 lease is the reliable target.
    """

    host: str = FALLBACK_HOST          # 192.168.25.219 (see docstring)
    media_width: int = 25              # 25 | 50 mm
    mode: str = "vivid"                # vivid | normal
    cut: str = "full"                  # none | half | full
    font: str | None = None            # default .ttf for text elements
    background: str = "white"          # default canvas background
    units: str = "in"                  # in | cm  (display only)

    @classmethod
    def load(cls) -> "WebSettings":
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            valid = set(cls.__dataclass_fields__)
            return cls(**{k: v for k, v in data.items() if k in valid})
        return cls()

    def save(self) -> None:
        ensure_runtime()
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def update(self, data: dict) -> "WebSettings":
        valid = set(self.__dataclass_fields__)
        for k, v in data.items():
            if k in valid:
                setattr(self, k, v)
        self.save()
        return self


def log_event(event: str, message: str = "", **fields) -> None:
    """Append one JSON object per line to ~/.labler/logs/events.jsonl.

    Always includes timestamp, event (dotted name), message; plus any extra fields.
    Never raises into the request path — logging must not break a print.
    """
    ensure_runtime()
    rec = {"timestamp": now_iso(), "event": event, "message": message, **fields}
    try:
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass
