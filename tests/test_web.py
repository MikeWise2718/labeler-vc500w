"""Integration tests for the Flask app — exercised via the test client with the
printer protocol monkeypatched (no real hardware). Runtime dir is redirected to a
tmp_path so tests never touch ~/.labler/."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from labler.web import app as webapp
from labler.web import runtime
from labler.status import Status


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
    from labler import render
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
    assert preview.width == 312 and preview.height == 600


def test_measure_endpoint(client):
    dl = {"media_mm": 25, "length_px": 600, "rotate": 90, "elements": []}
    m = client.post("/api/measure", json=dl).get_json()
    assert m["ok"]
    assert m["width_px"] == 312
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


def test_design_save_list_load_delete(client):
    dl = {"name": "My Label", "media_mm": 25, "length_px": 80, "elements": [
        {"type": "border", "thickness": 3}]}
    sid = client.post("/api/designs", json=dl).get_json()["id"]
    assert sid == "my-label"
    listing = client.get("/api/designs").get_json()["designs"]
    assert any(d["id"] == sid for d in listing)
    loaded = client.get("/api/designs/" + sid).get_json()
    assert loaded["name"] == "My Label"
    assert client.delete("/api/designs/" + sid).get_json()["ok"]
    assert client.get("/api/designs/" + sid).status_code == 404


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
    # history recorded the print, with orientation + tape stats + a thumbnail link
    hist = client.get("/api/history").get_json()["history"]
    assert len(hist) == 1
    h = hist[0]
    assert h["name"] == "t"
    assert h["orientation"] == "landscape"     # 312 wide x 80 long -> landscape
    assert h["tape_used_in"] == 2.5 and h["tape_used_cm"] is not None
    assert h["remain_before_in"] == 30.0 and h["remain_after_in"] == 27.5
    assert h["thumb"] == f"/api/history/{h['id']}/thumb.png"
    # thumbnail is served
    thumb = client.get(h["thumb"])
    assert thumb.status_code == 200 and thumb.mimetype == "image/png"
    # delete removes the entry (and its thumbnail)
    assert client.delete("/api/history/" + h["id"]).get_json()["ok"]
    assert client.get("/api/history").get_json()["history"] == []
    assert client.get(h["thumb"]).status_code == 404


def test_history_thumb_fallback_for_old_entry(client):
    # An entry written before thumbnails existed (no PNG file) still renders a
    # thumbnail from its stored display_list.
    import json as _json
    runtime.ensure_runtime()
    rec = {"id": "oldentry1234", "timestamp": "2026-06-10T00:00:00+00:00", "name": "old",
           "media_mm": 25, "bytes": 100,
           "display_list": {"media_mm": 25, "length_px": 80,
                            "elements": [{"type": "border", "thickness": 2}]}}
    runtime.HISTORY_FILE.write_text(_json.dumps(rec) + "\n", encoding="utf-8")
    # listing backfills orientation
    h = client.get("/api/history").get_json()["history"][0]
    assert h["orientation"] in ("portrait", "landscape", "square")
    # thumbnail falls back to rendering the display_list
    thumb = client.get("/api/history/oldentry1234/thumb.png")
    assert thumb.status_code == 200 and thumb.mimetype == "image/png"


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
    assert b"Labler" in r.data
