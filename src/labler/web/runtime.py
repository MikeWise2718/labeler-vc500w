"""Runtime data layout + helpers for the web app.

All mutable state lives under ~/.labler/ (NOT in the code repo) per the workspace
code/runtime split rule:

    ~/.labler/
      settings.json        # server-side app settings
      logs/events.jsonl    # structured event log (STATISTICS ONLY — see log_event)
      designs/<id>/        # saved display-lists + preview thumbnails
      history.jsonl        # append-only print log

The printer is SHARED between people, so label content must not accumulate here.
Bitmaps are inlined as data URIs by the client and never stored server-side; the
event log is allowlist-filtered. See specs/central-deployment.md.

The directory is created on first access. Settings here are the web app's
authority and are a superset of the CLI's ~/.config/labler/config.json.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import DEFAULT_HOST, FALLBACK_HOST  # noqa: F401  (re-exported for callers)

RUNTIME_DIR = Path.home() / ".labler"
SETTINGS_FILE = RUNTIME_DIR / "settings.json"
LOG_DIR = RUNTIME_DIR / "logs"
EVENTS_FILE = LOG_DIR / "events.jsonl"
# ASSETS_DIR was removed in v0.8.1 — uploaded bitmaps are label content and the
# printer is now shared, so images are inlined as data URIs instead of stored here.
# An existing ~/.labler/assets/ from an older version is left alone (not deleted);
# it is dead data the user can remove at leisure.
DESIGNS_DIR = RUNTIME_DIR / "designs"
HISTORY_FILE = RUNTIME_DIR / "history.jsonl"
HISTORY_DIR = RUNTIME_DIR / "history"       # per-print thumbnails: <entry_id>.png
STATS_FILE = RUNTIME_DIR / "stats.jsonl"    # shared tape accounting (NO label content)


def ensure_runtime() -> None:
    """Create the runtime directory tree if missing. Cheap; safe to call often."""
    for d in (RUNTIME_DIR, LOG_DIR, DESIGNS_DIR, HISTORY_DIR):
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
    custom_colors: list[str] = field(default_factory=list)  # extra preset swatches (hex)
    # Shelly smart outlet the printer is plugged into, for remote wedge recovery.
    # Empty host = feature disabled (no outlet wired up yet). See labler/power.py.
    shelly_host: str = ""              # e.g. "192.168.25.184"
    shelly_outlet: int = 0             # 0-3 on a Power Strip 4 Gen4

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


# ---- privacy: statistics-only logging ---------------------------------------
#
# The printer is SHARED between people; label content (text, images, design names)
# is private to whoever printed it and MUST NOT reach the server's event log. See
# specs/central-deployment.md "Privacy model".
#
# This is an ALLOWLIST, deliberately. A denylist — or discipline at the ~12 call
# sites — springs a leak the first time someone adds a debug field. Anything not
# named here is dropped before it touches disk. Adding a field is a conscious act
# that lands in this list, in a diff, where it can be reviewed.
LOG_FIELD_ALLOWLIST = frozenset({
    # identity / provenance
    "version", "host", "hostname", "kind", "id", "entry",
    # printer state + tape statistics (the whole point of the log)
    "ok", "state", "stage", "error", "remain", "remain_before", "remain_after",
    "tape_used_in", "cassette_type", "media", "media_mm", "mode", "cut",
    # geometry — dimensions are not content
    "w", "h", "width", "height", "length_px", "count", "keys", "elements",
    # queue / job bookkeeping
    "job", "position", "waited_s", "duration_s",
})

# Fields historically logged that DO carry label content. Named explicitly so the
# regression test can assert they stay out, and so a future reader knows these are
# excluded on purpose rather than by oversight.
LOG_FIELD_DENIED_CONTENT = frozenset({"name", "text", "src", "src_id", "display_list",
                                      "designs", "history", "thumb", "data"})


def _filter_log_fields(fields: dict) -> dict:
    """Drop any field not on LOG_FIELD_ALLOWLIST. See the allowlist comment."""
    return {k: v for k, v in fields.items() if k in LOG_FIELD_ALLOWLIST}


# An exception's str() can quote the data that caused it — a render error may embed
# the very label text we are trying to keep off the server. `message` is positional
# so it bypasses the field allowlist; cap it instead. The exception TYPE (logged as
# `kind`) is what actually aids debugging, not the interpolated detail.
MAX_MESSAGE_LEN = 200


# Exactly the fields a stats record may contain. Unlike the event log (where the
# allowlist is a safety net over free-form calls), this is the *complete schema* —
# a stats record is built here and nowhere else, so anything absent from this list
# cannot appear. Tape accounting is the one thing that SHOULD be shared: "who is
# burning the roll, when do we reorder" is a household question, and none of it is
# label content.
STATS_FIELDS = (
    "timestamp", "host", "media_mm", "mode", "cut", "ok", "error_kind",
    "remain_before_in", "remain_after_in", "tape_used_in", "bytes",
)


def record_print_stats(*, host: str, media_mm: int | None, mode: str | None,
                       cut: str | None, ok: bool, error_kind: str | None = None,
                       remain_before_in: float | None = None,
                       remain_after_in: float | None = None,
                       tape_used_in: float | None = None,
                       jpeg_bytes: int | None = None) -> None:
    """Append one statistics record per print attempt to ~/.labler/stats.jsonl.

    Keyword-only and explicitly typed so a caller cannot accidentally pass label
    content through. Never raises into the print path.
    """
    ensure_runtime()
    rec = {
        "timestamp": now_iso(), "host": host, "media_mm": media_mm,
        "mode": mode, "cut": cut, "ok": bool(ok), "error_kind": error_kind,
        "remain_before_in": remain_before_in, "remain_after_in": remain_after_in,
        "tape_used_in": tape_used_in, "bytes": jpeg_bytes,
    }
    assert set(rec) == set(STATS_FIELDS), "stats record drifted from STATS_FIELDS"
    try:
        with STATS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


def read_stats() -> list[dict]:
    """All stats records, oldest first. Returns [] if the file is missing."""
    if not STATS_FILE.exists():
        return []
    out = []
    for ln in STATS_FILE.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue          # a torn line must not break the whole report
    return out


def summarise_stats(records: list[dict]) -> dict:
    """Aggregate stats for the shared-usage view: totals + per-day tape used."""
    prints = len(records)
    ok = sum(1 for r in records if r.get("ok"))
    used = [r.get("tape_used_in") for r in records if isinstance(r.get("tape_used_in"), (int, float))]
    total_in = round(sum(used), 2) if used else 0.0
    by_day: dict[str, float] = {}
    for r in records:
        ts, t = r.get("timestamp"), r.get("tape_used_in")
        if isinstance(ts, str) and isinstance(t, (int, float)):
            by_day[ts[:10]] = round(by_day.get(ts[:10], 0.0) + t, 2)
    remains = [r.get("remain_after_in") for r in records
               if isinstance(r.get("remain_after_in"), (int, float))]
    # NB: "succeeded", not "ok" — the JSON envelope already uses `ok` for
    # request success, and two different meanings on one key is a trap.
    return {
        "prints": prints, "succeeded": ok, "failed": prints - ok,
        "tape_used_in": total_in, "tape_used_cm": round(total_in * 2.54, 1),
        "by_day": dict(sorted(by_day.items())),
        "last_remain_in": remains[-1] if remains else None,
        "first_timestamp": records[0].get("timestamp") if records else None,
        "last_timestamp": records[-1].get("timestamp") if records else None,
    }


def log_event(event: str, message: str = "", **fields) -> None:
    """Append one JSON object per line to ~/.labler/logs/events.jsonl.

    Always includes timestamp, event (dotted name), message; plus any extra fields
    that survive LOG_FIELD_ALLOWLIST — label content is dropped, not written.
    `message` is truncated to MAX_MESSAGE_LEN (see comment above).
    Never raises into the request path — logging must not break a print.
    """
    ensure_runtime()
    msg = str(message)
    if len(msg) > MAX_MESSAGE_LEN:
        msg = msg[:MAX_MESSAGE_LEN] + "…[truncated]"
    rec = {"timestamp": now_iso(), "event": event, "message": msg,
           **_filter_log_fields(fields)}
    try:
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass
