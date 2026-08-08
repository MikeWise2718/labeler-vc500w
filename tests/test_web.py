"""Integration tests for the Flask app — exercised via the test client with the
printer protocol monkeypatched (no real hardware). Runtime dir is redirected to a
tmp_path so tests never touch ~/.labeler/."""

from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

from labeler import compose
from labeler.config import media_for
from labeler.web import app as webapp
from labeler.web import runtime
from labeler.status import Status


@pytest.fixture
def client(tmp_path, monkeypatch):
    # redirect every runtime path into tmp_path
    monkeypatch.setattr(runtime, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(runtime, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(runtime, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(runtime, "EVENTS_FILE", tmp_path / "logs" / "events.jsonl")
    monkeypatch.setattr(runtime, "DESIGNS_DIR", tmp_path / "designs")
    monkeypatch.setattr(runtime, "HISTORY_FILE", tmp_path / "history.jsonl")
    monkeypatch.setattr(runtime, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(runtime, "STATS_FILE", tmp_path / "stats.jsonl")
    app = webapp.create_app()
    app.testing = True
    return app.test_client()


def test_ping_has_four_fields(client):
    r = client.get("/api/ping").get_json()
    assert set(r) >= {"hostname", "status", "timestamp", "version"}
    assert r["status"] == "ok"


def test_render_returns_png(client):
    dl = {"media_mm": 25, "length_px": 100, "elements": [
        {"type": "text", "x": 0, "y": 0, "w": 312, "h": 100, "text": "hi", "font_size": 40}]}
    r = client.post("/api/render", json=dl)
    assert r.status_code == 200
    assert r.mimetype == "image/png"
    Image.open(io.BytesIO(r.data))  # decodable


def test_render_honors_background_color(client):
    dl = {"media_mm": 25, "length_px": 100, "background": "#ffff00", "elements": []}
    r = client.post("/api/render", json=dl)
    assert r.status_code == 200
    im = Image.open(io.BytesIO(r.data)).convert("RGB")
    assert im.getpixel((2, 2)) == (255, 255, 0)  # top-left = chosen background


def test_fonts_endpoint_shape(client):
    r = client.get("/api/fonts").get_json()
    assert isinstance(r["fonts"], list) and r["fonts"]
    f = r["fonts"][0]
    assert {"name", "has_bold", "has_italic"} <= set(f)
    # legacy map lets the UI migrate raw .ttf font values to families
    assert isinstance(r["legacy"], dict)


def test_render_honors_bold_italic(client):
    base = {"type": "text", "x": 0, "y": 0, "w": 312, "h": 100,
            "text": "Abc", "font": "Arial", "font_size": 56, "color": "black"}
    def png(bold, italic):
        dl = {"media_mm": 25, "length_px": 120,
              "elements": [{**base, "bold": bold, "italic": italic}]}
        rv = client.post("/api/render", json=dl)
        assert rv.status_code == 200
        return rv.data
    plain = png(False, False)
    bold = png(True, False)
    # Bold changes the rendered glyphs -> different bytes (skip if Arial absent).
    from labeler import render
    if render._try_truetype("arialbd.ttf", 16) is not None:
        assert bold != plain


def test_render_image_without_source_is_ok_not_500(client):
    # Regression: adding an image element before picking a file used to KeyError -> 500.
    dl = {"media_mm": 25, "length_px": 100, "elements": [
        {"type": "image", "x": 0, "y": 0, "w": 100, "h": 80}]}
    r = client.post("/api/render", json=dl)
    assert r.status_code == 200
    assert r.mimetype == "image/png"


def test_render_sets_length_headers(client):
    dl = {"media_mm": 25, "length_px": 600, "elements": []}
    r = client.post("/api/render", json=dl)
    assert r.status_code == 200
    assert r.headers["X-Label-Length-Px"] == "600"
    assert float(r.headers["X-Label-Length-Cm"]) > 0


def test_render_preview_matches_print_orientation(client):
    # The preview render IS the print render: 25mm width = image width (312),
    # length = image height. No view rotation. (Regression: a separate tape-view
    # rotation once made the preview disagree with what printed.)
    dl = {"media_mm": 25, "length_px": 600, "elements": [
        {"type": "text", "x": 0, "y": 0, "w": 312, "h": 100, "text": "X", "font_size": 60}]}
    preview = Image.open(io.BytesIO(client.post("/api/render", json=dl).data))
    # 312 content + the right-edge bleed; preview == print, so it carries the bleed too.
    assert preview.width == media_for(25).width_px + compose._RIGHT_BLEED_PX
    assert preview.height == 600


def test_measure_endpoint(client):
    dl = {"media_mm": 25, "length_px": 600, "rotate": 90, "elements": []}
    m = client.post("/api/measure", json=dl).get_json()
    assert m["ok"]
    assert m["width_px"] == media_for(25).width_px + compose._RIGHT_BLEED_PX
    assert m["length_px"] < 600  # rotated short


def test_settings_roundtrip(client):
    r = client.post("/api/settings", json={"host": "10.0.0.9", "media_width": 50})
    assert r.get_json()["ok"]
    got = client.get("/api/settings").get_json()
    assert got["settings"]["host"] == "10.0.0.9"
    assert got["settings"]["media_width"] == 50


def test_custom_colors_roundtrip(client):
    # defaults to an empty list, then persists what we set
    got = client.get("/api/settings").get_json()
    assert got["settings"]["custom_colors"] == []
    r = client.post("/api/settings", json={"custom_colors": ["#112233", "#aabbcc"]})
    assert r.get_json()["ok"]
    got = client.get("/api/settings").get_json()
    assert got["settings"]["custom_colors"] == ["#112233", "#aabbcc"]


def test_asset_endpoints_are_gone(client):
    """v0.8.1 removed /api/assets — bitmaps are inlined as data URIs so that image
    content never lands on the shared server. See specs/central-deployment.md."""
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), "red").save(buf, "PNG")
    buf.seek(0)
    assert client.post("/api/assets", data={"file": (buf, "x.png")},
                       content_type="multipart/form-data").status_code == 404
    assert client.get("/api/assets/anything.png").status_code == 404


def test_render_accepts_inline_data_uri(client):
    """An image element carrying a data URI renders without any server-side asset."""
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), "red").save(buf, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    dl = {"media_mm": 25, "length_px": 60, "elements": [
        {"type": "image", "x": 0, "y": 0, "w": 40, "h": 20, "src": uri, "fit": "contain"}]}
    r = client.post("/api/render", json=dl)
    assert r.status_code == 200
    im = Image.open(io.BytesIO(r.data)).convert("RGB")
    assert im.getpixel((5, 5)) == (255, 0, 0)   # the inlined red bitmap actually drew


def test_render_rejects_malformed_data_uri(client):
    dl = {"media_mm": 25, "length_px": 60, "elements": [
        {"type": "image", "x": 0, "y": 0, "w": 40, "h": 20,
         "src": "data:image/png;base64,!!!not-base64!!!", "fit": "contain"}]}
    r = client.post("/api/render", json=dl)
    assert r.status_code == 400
    assert not r.get_json()["ok"]


def test_design_endpoints_are_gone(client):
    """v0.8.3 moved designs into the browser (IndexedDB) — they are label content
    and the printer is shared. See specs/central-deployment.md."""
    dl = {"name": "My Label", "media_mm": 25, "length_px": 80, "elements": []}
    assert client.post("/api/designs", json=dl).status_code == 404
    assert client.get("/api/designs").status_code == 404
    assert client.get("/api/designs/my-label").status_code == 404
    assert client.delete("/api/designs/my-label").status_code == 404


def test_history_endpoints_are_gone(client):
    assert client.get("/api/history").status_code == 404
    assert client.get("/api/history/abc123/thumb.png").status_code == 404
    assert client.delete("/api/history/abc123").status_code == 404


def test_print_flow_monkeypatched(client, monkeypatch):
    sent = {}

    def fake_print(host, jpeg, **kw):
        sent["host"] = host
        sent["bytes"] = len(jpeg)
        return Status(print_state="IDLE", print_job_stage="SUCCESS",
                      print_job_error="NONE", remain=27.5)   # remain AFTER print

    # get_status is queried for remain BEFORE the print -> tape used = 30.0 - 27.5
    monkeypatch.setattr(webapp.protocol, "get_status",
                        lambda host, **k: Status(print_state="IDLE", remain=30.0))
    monkeypatch.setattr(webapp.protocol, "print_jpeg", fake_print)
    dl = {"name": "t", "media_mm": 25, "length_px": 80,
          "elements": [{"type": "text", "x": 0, "y": 0, "w": 312, "h": 80, "text": "go"}]}
    r = client.post("/api/print", json=dl).get_json()
    assert r["ok"] is True
    assert sent["bytes"] > 0
    # TRUE tape used from the hardware before/after remain delta
    assert r["remain_before"] == 30.0 and r["remain_after"] == 27.5
    assert r["tape_used_in"] == 2.5
    # the response carries an entry id for the CLIENT to key its own history on
    assert r["entry"] and isinstance(r["entry"], str)
    # ...but the server wrote NO history record and NO thumbnail: that is label
    # content, and the printer is shared (specs/central-deployment.md).
    assert not runtime.HISTORY_FILE.exists(), "server must not write history.jsonl"
    assert list(runtime.HISTORY_DIR.glob("*.png")) == [], "server must not write thumbnails"
    # what it DID write is the statistics row
    stats = client.get("/api/stats").get_json()
    assert stats["prints"] == 1 and stats["tape_used_in"] == 2.5


def test_status_endpoint_monkeypatched(client, monkeypatch):
    monkeypatch.setattr(webapp.protocol, "get_status",
                        lambda host, **k: Status(print_state="IDLE", print_job_stage="READY",
                                                  print_job_error="NONE", remain=12.0))
    r = client.get("/api/status").get_json()
    assert r["ok"] and r["ready"] is True
    assert r["remain_in"] == 12.0 and r["remain_cm"] == 30.5


def test_about_endpoint(client):
    r = client.get("/api/about").get_json()
    assert r["version"] and r["python"] and "runtime_dir" in r


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Labeler" in r.data


def test_stats_endpoint_empty(client):
    s = client.get("/api/stats").get_json()
    assert s["ok"] and s["prints"] == 0 and s["tape_used_in"] == 0.0


def test_stats_endpoint_after_print(client, monkeypatch):
    """A print records a stats row; /api/stats aggregates it and leaks no content."""
    from labeler import protocol
    from labeler.status import Status

    def fake_status(host, **kw):
        return Status(raw={}, print_state="IDLE", print_job_stage="READY FOR PRINT",
                      print_job_error="NONE", remain=10.0, cassette_type=1)

    def fake_print(host, jpeg, **kw):
        return Status(raw={}, print_state="IDLE", print_job_stage="READY FOR PRINT",
                      print_job_error="NONE", remain=9.0, cassette_type=1)

    monkeypatch.setattr(protocol, "get_status", fake_status)
    monkeypatch.setattr(protocol, "print_jpeg", fake_print)

    dl = {"name": "Secret Label Name", "media_mm": 25, "length_px": 80,
          "elements": [{"type": "text", "x": 0, "y": 0, "w": 312, "h": 60,
                        "text": "CONFIDENTIAL-TEXT", "font_size": 30}]}
    r = client.post("/api/print", json=dl).get_json()
    assert r["ok"]

    s = client.get("/api/stats").get_json()
    assert s["prints"] == 1 and s["succeeded"] == 1
    assert s["tape_used_in"] == 1.0            # 10.0 -> 9.0 remain delta
    # the shared stats view must not carry the label's text or name
    blob = json.dumps(s)
    assert "CONFIDENTIAL-TEXT" not in blob and "Secret Label Name" not in blob


def test_migrate_export_returns_legacy_designs_and_history(client):
    """Upgrading must not silently lose saved designs. The one-shot export hands
    a pre-0.8.3 ~/.labeler/ to the browser for import into IndexedDB."""
    runtime.ensure_runtime()
    # a legacy saved design, as v0.7.x wrote it
    ddir = runtime.DESIGNS_DIR / "my-label"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "design.json").write_text(json.dumps(
        {"id": "my-label", "name": "My Label", "media_mm": 25,
         "elements": [{"type": "text", "text": "hello"}]}), encoding="utf-8")
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "PNG")
    (ddir / "preview.png").write_bytes(buf.getvalue())
    # a legacy history entry + thumbnail
    runtime.HISTORY_FILE.write_text(json.dumps(
        {"id": "abc123", "timestamp": "2026-06-10T00:00:00+00:00", "name": "old print",
         "media_mm": 25, "tape_used_in": 1.5,
         "display_list": {"media_mm": 25, "elements": []}}) + "\n", encoding="utf-8")
    (runtime.HISTORY_DIR / "abc123.png").write_bytes(buf.getvalue())

    r = client.get("/api/migrate/export").get_json()
    assert r["ok"]
    d = next(x for x in r["designs"] if x["id"] == "my-label")
    assert d["name"] == "My Label"
    assert d["display_list"]["elements"][0]["text"] == "hello"
    assert d["preview"].startswith("data:image/png;base64,")
    h = next(x for x in r["history"] if x["id"] == "abc123")
    assert h["tape_used_in"] == 1.5
    assert h["thumb"].startswith("data:image/png;base64,")


def test_migrate_export_is_read_only(client):
    """The export must leave the files in place so a failed import can be retried."""
    runtime.ensure_runtime()
    ddir = runtime.DESIGNS_DIR / "keepme"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "design.json").write_text(json.dumps({"id": "keepme", "name": "Keep"}),
                                      encoding="utf-8")
    client.get("/api/migrate/export")
    assert (ddir / "design.json").exists()


def test_migrate_export_empty_runtime_is_ok(client):
    r = client.get("/api/migrate/export").get_json()
    assert r["ok"] and r["designs"] == [] and r["history"] == []


def test_queue_endpoint_idle(client):
    r = client.get("/api/queue").get_json()
    assert r["ok"] and r["busy"] is False and r["waiting"] == 0


def test_print_reports_queue_position(client, monkeypatch):
    """A print that did not wait reports queued_behind=0."""
    from labeler import protocol
    from labeler.status import Status
    monkeypatch.setattr(protocol, "get_status",
                        lambda h, **k: Status(raw={}, print_state="IDLE",
                                              print_job_stage="READY", print_job_error="NONE",
                                              remain=10.0, cassette_type=1))
    monkeypatch.setattr(protocol, "print_jpeg",
                        lambda h, j, **k: Status(raw={}, print_state="IDLE",
                                                 print_job_stage="READY", print_job_error="NONE",
                                                 remain=9.0, cassette_type=1))
    dl = {"media_mm": 25, "length_px": 60, "elements": []}
    r = client.post("/api/print", json=dl).get_json()
    assert r["ok"] and r["queued_behind"] == 0


def test_concurrent_prints_serialize_through_http(client, monkeypatch):
    """Two overlapping /api/print calls must not touch the printer at once.

    This is the whole reason a single server owns :9100 — see CLAUDE.md.
    """
    import threading
    import time
    from labeler import protocol
    from labeler.status import Status

    concurrent = 0
    max_concurrent = 0
    guard = threading.Lock()

    def slow_print(host, jpeg, **kw):
        nonlocal concurrent, max_concurrent
        with guard:
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
        time.sleep(0.05)
        with guard:
            concurrent -= 1
        return Status(raw={}, print_state="IDLE", print_job_stage="READY",
                      print_job_error="NONE", remain=9.0, cassette_type=1)

    monkeypatch.setattr(protocol, "get_status",
                        lambda h, **k: Status(raw={}, print_state="IDLE",
                                              print_job_stage="READY", print_job_error="NONE",
                                              remain=10.0, cassette_type=1))
    monkeypatch.setattr(protocol, "print_jpeg", slow_print)

    dl = {"media_mm": 25, "length_px": 60, "elements": []}
    errors = []

    def do_print():
        try:
            client.post("/api/print", json=dl)
        except Exception as e:      # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=do_print) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors
    assert max_concurrent == 1, f"printer touched by {max_concurrent} requests at once"


def test_powercycle_requires_explicit_confirmation(client):
    """Cutting mains power is destructive — never on an unconfirmed request."""
    assert client.post("/api/device/powercycle", json={}).status_code == 400
    assert client.post("/api/device/powercycle", json={"confirm": False}).status_code == 400
    assert client.post("/api/device/powercycle", json={"confirm": "yes"}).status_code == 400


def test_powercycle_without_configured_outlet_is_a_clear_error(client):
    r = client.post("/api/device/powercycle", json={"confirm": True})
    assert r.status_code == 400
    body = r.get_json()
    assert not body["ok"] and "Shelly" in body["error"]
    assert "by hand" in body["hint"]        # tell the user what to do instead


def test_powercycle_happy_path(client, monkeypatch):
    from labeler import power
    calls = []
    monkeypatch.setattr(power, "power_cycle",
                        lambda host, outlet, **kw: calls.append((host, outlet))
                        or {"host": host, "outlet": outlet, "was_on": True,
                            "off_seconds": 8.0})
    client.post("/api/settings", json={"shelly_host": "1.2.3.4", "shelly_outlet": 2})
    r = client.post("/api/device/powercycle", json={"confirm": True}).get_json()
    assert r["ok"] and r["outlet"] == 2
    assert calls == [("1.2.3.4", 2)]
    assert "20 s" in r["hint"]              # tell the user to wait before printing


def test_powercycle_surfaces_shelly_failure(client, monkeypatch):
    from labeler import power
    from labeler.errors import LabelerError

    def boom(host, outlet, **kw):
        raise LabelerError("Shelly at 1.2.3.4 unreachable: timed out")

    monkeypatch.setattr(power, "power_cycle", boom)
    client.post("/api/settings", json={"shelly_host": "1.2.3.4", "shelly_outlet": 0})
    r = client.post("/api/device/powercycle", json={"confirm": True})
    assert r.status_code == 502
    assert "unreachable" in r.get_json()["error"]


def test_powercycle_does_not_log_label_content(client, monkeypatch):
    """The power-cycle log line is statistics only, like everything else."""
    from labeler import power
    monkeypatch.setattr(power, "power_cycle",
                        lambda host, outlet, **kw: {"host": host, "outlet": outlet,
                                                    "was_on": True, "off_seconds": 8.0})
    client.post("/api/settings", json={"shelly_host": "1.2.3.4", "shelly_outlet": 0})
    client.post("/api/device/powercycle", json={"confirm": True})
    log = runtime.EVENTS_FILE.read_text(encoding="utf-8")
    assert "powercycle" in log
    for forbidden in runtime.LOG_FIELD_DENIED_CONTENT:
        assert f'"{forbidden}"' not in log
