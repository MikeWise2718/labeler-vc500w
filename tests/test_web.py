"""Integration tests for the Flask app — exercised via the test client with the
printer protocol monkeypatched (no real hardware). Runtime dir is redirected to a
tmp_path so tests never touch ~/.labler/."""

from __future__ import annotations

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
    monkeypatch.setattr(runtime, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(runtime, "DESIGNS_DIR", tmp_path / "designs")
    monkeypatch.setattr(runtime, "HISTORY_FILE", tmp_path / "history.jsonl")
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


def test_render_view_tape_is_landscape(client):
    # Portrait normal render (312 wide x long); ?view=tape returns it rotated to
    # landscape so the tape <img> displays natively. Headers stay in true axes.
    dl = {"media_mm": 25, "length_px": 600, "elements": [
        {"type": "text", "x": 0, "y": 0, "w": 312, "h": 100, "text": "X", "font_size": 60}]}
    normal = Image.open(io.BytesIO(client.post("/api/render", json=dl).data))
    tape = Image.open(io.BytesIO(client.post("/api/render?view=tape", json=dl).data))
    assert normal.width == 312 and normal.height == 600       # portrait
    assert tape.width == 600 and tape.height == 312           # landscape (swapped)


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


def test_asset_upload_and_serve(client):
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), "red").save(buf, "PNG")
    buf.seek(0)
    r = client.post("/api/assets", data={"file": (buf, "x.png")},
                    content_type="multipart/form-data").get_json()
    assert r["ok"] and r["w"] == 20 and r["h"] == 10
    served = client.get("/api/assets/" + r["id"])
    assert served.status_code == 200


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
                      print_job_error="NONE", remain=30.0)

    monkeypatch.setattr(webapp.protocol, "print_jpeg", fake_print)
    dl = {"name": "t", "media_mm": 25, "length_px": 80,
          "elements": [{"type": "text", "x": 0, "y": 0, "w": 312, "h": 80, "text": "go"}]}
    r = client.post("/api/print", json=dl).get_json()
    assert r["ok"] is True
    assert sent["bytes"] > 0
    # history recorded the print
    hist = client.get("/api/history").get_json()["history"]
    assert len(hist) == 1 and hist[0]["name"] == "t"
    # delete it
    assert client.delete("/api/history/" + hist[0]["id"]).get_json()["ok"]
    assert client.get("/api/history").get_json()["history"] == []


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
