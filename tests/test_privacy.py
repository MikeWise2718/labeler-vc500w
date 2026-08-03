"""Privacy tests — the server must log STATISTICS ONLY, never label content.

The VC-500W is shared between people (specs/central-deployment.md). Label text,
image data and design names belong to whoever printed them and must not accumulate
in the server's event log. These tests are the enforcement: if someone widens
log_event's field set, a test here fails.
"""

from __future__ import annotations

import json

import pytest

from labler.web import runtime


@pytest.fixture
def logfile(tmp_path, monkeypatch):
    """Redirect the event log into tmp_path and hand back a reader."""
    monkeypatch.setattr(runtime, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(runtime, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(runtime, "EVENTS_FILE", tmp_path / "logs" / "events.jsonl")

    def records() -> list[dict]:
        f = tmp_path / "logs" / "events.jsonl"
        if not f.exists():
            return []
        return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln]

    return records


# ---- the allowlist ---------------------------------------------------------

def test_allowed_fields_survive(logfile):
    runtime.log_event("print.done", "printed", host="1.2.3.4", ok=True,
                      tape_used_in=1.25, remain=42.0)
    rec = logfile()[0]
    assert rec["host"] == "1.2.3.4"
    assert rec["ok"] is True
    assert rec["tape_used_in"] == 1.25
    assert rec["remain"] == 42.0


def test_unknown_field_is_dropped(logfile):
    """A field nobody put on the allowlist must not reach disk."""
    runtime.log_event("some.event", "msg", made_up_debug_field="sensitive")
    rec = logfile()[0]
    assert "made_up_debug_field" not in rec
    assert "sensitive" not in json.dumps(rec)


def test_envelope_always_present(logfile):
    runtime.log_event("app.start", "web app started", version="9.9.9")
    rec = logfile()[0]
    assert set(rec) >= {"timestamp", "event", "message", "version"}
    assert rec["event"] == "app.start"


@pytest.mark.parametrize("field", sorted(runtime.LOG_FIELD_DENIED_CONTENT))
def test_content_bearing_fields_never_logged(logfile, field):
    """Every field known to carry label content is dropped.

    Parametrised so adding a name to LOG_FIELD_DENIED_CONTENT automatically gets
    it covered.
    """
    runtime.log_event("design.save", "saved design", **{field: "SECRET-LABEL-TEXT"})
    rec = logfile()[0]
    assert field not in rec
    assert "SECRET-LABEL-TEXT" not in json.dumps(rec)


def test_allowlist_and_denylist_do_not_overlap():
    """A field cannot be both permitted and known-content — that would be a bug."""
    assert not (runtime.LOG_FIELD_ALLOWLIST & runtime.LOG_FIELD_DENIED_CONTENT)


# ---- regression: the specific leaks found 2026-08-03 -----------------------

def test_design_save_does_not_log_label_name(logfile):
    """REGRESSION: app.py:266 logged name=<label name>, which is label content."""
    runtime.log_event("design.save", "saved design", id="abc123", name="Mike's home address")
    rec = logfile()[0]
    assert rec["id"] == "abc123"          # the id is fine — not content
    assert "name" not in rec
    assert "Mike" not in json.dumps(rec)


def test_print_done_entry_id_is_kept(logfile):
    """`entry` is an entry_id STRING (not the record), so it stays — it is an id.

    Guards the opposite mistake: over-tightening the allowlist and losing the
    ability to correlate a log line with a client-side history entry.
    """
    runtime.log_event("print.done", "printed", entry="20260803-abc123", ok=True)
    assert logfile()[0]["entry"] == "20260803-abc123"


# ---- message truncation ----------------------------------------------------

def test_long_message_is_truncated(logfile):
    """`message` is positional and skips the field filter; an exception str() can
    quote the label text that caused it. Cap it."""
    runtime.log_event("render.error", "X" * 5000, kind="ValueError")
    rec = logfile()[0]
    assert len(rec["message"]) <= runtime.MAX_MESSAGE_LEN + len("…[truncated]")
    assert rec["message"].endswith("[truncated]")
    assert rec["kind"] == "ValueError"    # the useful part survives


def test_short_message_untouched(logfile):
    runtime.log_event("device.reset", "status re-queried", host="h")
    assert logfile()[0]["message"] == "status re-queried"


def test_non_string_message_does_not_crash(logfile):
    runtime.log_event("odd.event", 12345)
    assert logfile()[0]["message"] == "12345"


# ---- logging must never break a print --------------------------------------

def test_log_event_survives_unwritable_log(tmp_path, monkeypatch):
    """A logging failure must not propagate into the print path."""
    monkeypatch.setattr(runtime, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(runtime, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(runtime, "EVENTS_FILE", tmp_path / "logs" / "events.jsonl")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(runtime.Path, "open", boom)
    runtime.log_event("print.done", "printed", ok=True)   # must not raise


def test_unserialisable_value_does_not_raise(logfile):
    """default=str keeps odd objects from exploding the log."""
    class Weird:
        def __str__(self):
            return "weird-obj"

    runtime.log_event("print.done", "printed", state=Weird())
    assert logfile()[0]["state"] == "weird-obj"


# ---- stats stream ----------------------------------------------------------
# Tape accounting is the one dataset that SHOULD be shared. These tests pin both
# halves of that: the numbers are right, and no label content rides along.

@pytest.fixture
def statsfile(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(runtime, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(runtime, "EVENTS_FILE", tmp_path / "logs" / "events.jsonl")
    monkeypatch.setattr(runtime, "DESIGNS_DIR", tmp_path / "designs")
    monkeypatch.setattr(runtime, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(runtime, "STATS_FILE", tmp_path / "stats.jsonl")
    return tmp_path / "stats.jsonl"


def test_stats_record_shape_is_exactly_the_schema(statsfile):
    runtime.record_print_stats(host="h", media_mm=25, mode="vivid", cut="full",
                               ok=True, tape_used_in=1.5)
    rec = json.loads(statsfile.read_text(encoding="utf-8").splitlines()[0])
    assert set(rec) == set(runtime.STATS_FIELDS)


def test_stats_contain_no_label_content(statsfile):
    """The schema is closed — there is no field a caller could smuggle text into."""
    runtime.record_print_stats(host="h", media_mm=25, mode="vivid", cut="full",
                               ok=True, tape_used_in=1.0)
    blob = statsfile.read_text(encoding="utf-8")
    for forbidden in runtime.LOG_FIELD_DENIED_CONTENT:
        assert f'"{forbidden}"' not in blob


def test_stats_record_is_keyword_only():
    """Positional args would let content slide in by position. Must be kw-only."""
    with pytest.raises(TypeError):
        runtime.record_print_stats("host", 25)          # type: ignore[misc]


def test_summarise_totals(statsfile):
    for used, ok in [(1.0, True), (2.0, True), (0.5, False)]:
        runtime.record_print_stats(host="h", media_mm=25, mode="vivid", cut="full",
                                   ok=ok, tape_used_in=used)
    s = runtime.summarise_stats(runtime.read_stats())
    assert s["prints"] == 3
    assert s["succeeded"] == 2 and s["failed"] == 1
    assert s["tape_used_in"] == 3.5
    assert s["tape_used_cm"] == 8.9        # 3.5 in * 2.54


def test_summarise_groups_by_day(statsfile):
    runtime.record_print_stats(host="h", media_mm=25, mode="vivid", cut="full",
                               ok=True, tape_used_in=1.25)
    s = runtime.summarise_stats(runtime.read_stats())
    assert len(s["by_day"]) == 1
    assert list(s["by_day"].values())[0] == 1.25


def test_summarise_empty_is_safe():
    s = runtime.summarise_stats([])
    assert s["prints"] == 0 and s["tape_used_in"] == 0.0
    assert s["by_day"] == {} and s["last_remain_in"] is None


def test_read_stats_skips_torn_line(statsfile):
    runtime.record_print_stats(host="h", media_mm=25, mode="vivid", cut="full", ok=True,
                               tape_used_in=1.0)
    with statsfile.open("a", encoding="utf-8") as f:
        f.write('{"partial": tru\n')          # torn write, e.g. power loss
    runtime.record_print_stats(host="h", media_mm=25, mode="vivid", cut="full", ok=True,
                               tape_used_in=2.0)
    recs = runtime.read_stats()
    assert len(recs) == 2                     # the torn line is skipped, not fatal
    assert runtime.summarise_stats(recs)["tape_used_in"] == 3.0


def test_stats_missing_tape_value_does_not_break_totals(statsfile):
    runtime.record_print_stats(host="h", media_mm=25, mode="vivid", cut="full",
                               ok=False, error_kind="LablerError")   # no tape figures
    s = runtime.summarise_stats(runtime.read_stats())
    assert s["prints"] == 1 and s["tape_used_in"] == 0.0
