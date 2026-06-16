"""Flask app factory + routes for the VC-500W label designer.

Thin HTTP layer over the verified core. Conventions (workspace Flask rules):
  - version in header from labler.__version__
  - GET /api/ping with {hostname, status, timestamp, version}
  - every UI action hits /api/... returning JSON (preview/asset endpoints return bytes)
  - runtime data under ~/.labler/ (see runtime.py), structured JSONL event log
  - settings persisted server-side

Printer access is SERIALIZED with a module-level lock: the VC-500W accepts only one
:9100 connection at a time, so two browser tabs must not collide. Status and print
both take the lock.
"""

from __future__ import annotations

import hashlib
import json
import platform
import socket
import threading
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from .. import __version__, compose, protocol
from ..config import MEDIA, SUPPORTED_WIDTHS, media_for
from ..errors import LablerError
from ..render import _load_font  # for font availability probing
from . import runtime
from .runtime import WebSettings, log_event

# One printer at a time. Held across the whole status/print op.
_printer_lock = threading.Lock()


def _settings() -> WebSettings:
    return WebSettings.load()


def _slug(s: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_" else "-" for c in s.strip().lower())
    return keep.strip("-") or "design"


def create_app() -> Flask:
    runtime.ensure_runtime()
    app = Flask(__name__, static_folder="static", template_folder="templates")
    log_event("app.start", "web app started", version=__version__)

    # ---- pages -----------------------------------------------------------------
    @app.get("/")
    def index():
        # Single-page tabbed UI. Version injected for the header.
        return send_from_directory(app.template_folder, "index.html")

    # ---- health ----------------------------------------------------------------
    @app.get("/api/ping")
    def ping():
        return jsonify(
            hostname=socket.gethostname(),
            status="ok",
            timestamp=runtime.now_iso(),
            version=__version__,
        )

    # ---- printer status / device ----------------------------------------------
    @app.get("/api/status")
    def api_status():
        host = _settings().host
        try:
            with _printer_lock:
                st = protocol.get_status(host)
        except LablerError as e:
            return jsonify(ok=False, error=str(e), host=host), 502
        return jsonify(ok=True, host=host, **_status_dict(st))

    @app.get("/api/device")
    def api_device():
        host = _settings().host
        n_prints, last = _history_summary()
        try:
            with _printer_lock:
                st = protocol.get_status(host)
        except LablerError as e:
            return jsonify(ok=False, error=str(e), host=host,
                           total_prints=n_prints, last_printed=last), 502
        cas = st.cassette_type
        media_name = next((m.name for m in MEDIA.values() if m.cassette_type == cas), None)
        return jsonify(
            ok=True, host=host, total_prints=n_prints, last_printed=last,
            media_name=media_name, raw=st.raw, **_status_dict(st),
        )

    @app.post("/api/reset")
    def api_reset():
        """Best-effort wake/clear: re-query status. A truly wedged printer needs a
        physical power-cycle (documented in CLAUDE.md); we surface that."""
        host = _settings().host
        try:
            with _printer_lock:
                st = protocol.get_status(host)
        except LablerError as e:
            log_event("device.reset_failed", str(e), host=host)
            return jsonify(ok=False, error=str(e),
                           hint="If status keeps failing, power-cycle the printer."), 502
        log_event("device.reset", "status re-queried", host=host, state=st.print_state)
        wedged = st.print_state in ("BUSY", "ERROR") and not st.ready
        return jsonify(ok=True, wedged=wedged,
                       hint="Power-cycle the printer to clear a wedge." if wedged else None,
                       **_status_dict(st))

    # ---- render / print --------------------------------------------------------
    @app.post("/api/render")
    def api_render():
        """Body = display-list JSON -> PNG preview bytes (the WYSIWYG preview)."""
        dl = _resolve_assets(request.get_json(force=True))
        try:
            png = compose.render_display_list(dl, fmt="PNG")
            dims = compose.measure_display_list(dl)
        except Exception as e:  # malformed display-list -> 400, never a 500
            log_event("render.error", str(e), kind=type(e).__name__)
            return jsonify(ok=False, error=f"{type(e).__name__}: {e}"), 400
        from flask import Response
        resp = Response(png, mimetype="image/png")
        # Physical size travels in headers so the UI can show "Tape used: X cm"
        # without decoding the PNG or making a second request.
        resp.headers["X-Label-Width-Px"] = str(dims["width_px"])
        resp.headers["X-Label-Length-Px"] = str(dims["length_px"])
        resp.headers["X-Label-Length-Cm"] = str(dims["length_cm"])
        resp.headers["X-Label-Length-In"] = str(dims["length_in"])
        resp.headers["Access-Control-Expose-Headers"] = (
            "X-Label-Width-Px,X-Label-Length-Px,X-Label-Length-Cm,X-Label-Length-In"
        )
        return resp

    @app.post("/api/measure")
    def api_measure():
        """Body = display-list -> physical dimensions (px/mm/cm/in) without rendering."""
        dl = _resolve_assets(request.get_json(force=True))
        try:
            return jsonify(ok=True, **compose.measure_display_list(dl))
        except Exception as e:
            return jsonify(ok=False, error=f"{type(e).__name__}: {e}"), 400

    @app.post("/api/print")
    def api_print():
        body = request.get_json(force=True)
        dl = _resolve_assets(body)
        s = _settings()
        try:
            jpeg = compose.render_display_list(dl, fmt="JPEG")
        except Exception as e:
            log_event("render.error", str(e), kind=type(e).__name__)
            return jsonify(ok=False, error=f"{type(e).__name__}: {e}"), 400
        mode = body.get("mode", s.mode)
        cut = body.get("cut", s.cut)
        try:
            with _printer_lock:
                st = protocol.print_jpeg(s.host, jpeg, mode=mode, cut=cut)
        except LablerError as e:
            log_event("print.failed", str(e), host=s.host)
            return jsonify(ok=False, error=str(e)), 502
        ok = st.print_job_error in (None, "NONE")
        entry = _append_history(body, jpeg, st)
        log_event("print.done", "printed", host=s.host, ok=ok,
                  state=st.print_state, remain=st.remain, entry=entry)
        return jsonify(ok=ok, entry=entry, **_status_dict(st))

    # ---- assets (uploaded bitmaps) --------------------------------------------
    @app.post("/api/assets")
    def api_upload_asset():
        if "file" not in request.files:
            return jsonify(ok=False, error="no file field"), 400
        f = request.files["file"]
        data = f.read()
        if not data:
            return jsonify(ok=False, error="empty file"), 400
        ext = (Path(f.filename or "").suffix or ".png").lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):
            return jsonify(ok=False, error=f"unsupported type {ext}"), 400
        aid = hashlib.sha1(data).hexdigest()[:16] + ext
        path = runtime.ASSETS_DIR / aid
        runtime.ensure_runtime()
        path.write_bytes(data)
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        log_event("asset.upload", "uploaded asset", id=aid, w=w, h=h)
        return jsonify(ok=True, id=aid, w=w, h=h)

    @app.get("/api/assets/<aid>")
    def api_get_asset(aid):
        path = runtime.ASSETS_DIR / Path(aid).name
        if not path.exists():
            abort(404)
        return send_file(path)

    # ---- designs (saved display-lists) ----------------------------------------
    @app.get("/api/designs")
    def api_list_designs():
        out = []
        for d in sorted(runtime.DESIGNS_DIR.glob("*/"), key=lambda p: p.stat().st_mtime,
                        reverse=True):
            meta = d / "design.json"
            if not meta.exists():
                continue
            try:
                dl = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({
                "id": d.name,
                "name": dl.get("name", d.name),
                "mtime": d.stat().st_mtime,
                "preview": f"/api/designs/{d.name}/preview.png" if (d / "preview.png").exists() else None,
            })
        return jsonify(designs=out)

    @app.get("/api/designs/<did>")
    def api_get_design(did):
        path = runtime.DESIGNS_DIR / _slug(did) / "design.json"
        if not path.exists():
            abort(404)
        return jsonify(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/designs/<did>/preview.png")
    def api_design_preview(did):
        path = runtime.DESIGNS_DIR / _slug(did) / "preview.png"
        if not path.exists():
            abort(404)
        return send_file(path)

    @app.post("/api/designs")
    def api_save_design():
        dl = request.get_json(force=True)
        name = dl.get("name") or "design"
        did = _slug(dl.get("id") or name)
        ddir = runtime.DESIGNS_DIR / did
        ddir.mkdir(parents=True, exist_ok=True)
        dl["id"] = did
        (ddir / "design.json").write_text(json.dumps(dl, indent=2), encoding="utf-8")
        try:
            png = compose.render_display_list(_resolve_assets(dict(dl)), fmt="PNG")
            (ddir / "preview.png").write_bytes(png)
        except (ValueError, OSError):
            pass
        log_event("design.save", "saved design", id=did, name=name)
        return jsonify(ok=True, id=did)

    @app.delete("/api/designs/<did>")
    def api_delete_design(did):
        ddir = runtime.DESIGNS_DIR / _slug(did)
        if not ddir.exists():
            abort(404)
        import shutil
        shutil.rmtree(ddir)
        log_event("design.delete", "deleted design", id=did)
        return jsonify(ok=True)

    # ---- history ---------------------------------------------------------------
    @app.get("/api/history")
    def api_history():
        return jsonify(history=_read_history())

    @app.delete("/api/history/<entry>")
    def api_delete_history(entry):
        items = [h for h in _read_history() if h.get("id") != entry]
        _write_history(items)
        log_event("history.delete", "deleted history entry", id=entry)
        return jsonify(ok=True)

    # ---- settings --------------------------------------------------------------
    @app.get("/api/settings")
    def api_get_settings():
        from dataclasses import asdict
        s = _settings()
        return jsonify(settings=asdict(s),
                       media_widths=list(SUPPORTED_WIDTHS),
                       media={str(w): m.name for w, m in MEDIA.items()})

    @app.post("/api/settings")
    def api_set_settings():
        from dataclasses import asdict
        data = request.get_json(force=True)
        s = _settings().update(data)
        log_event("settings.update", "settings changed", keys=list(data.keys()))
        return jsonify(ok=True, settings=asdict(s))

    # ---- fonts -----------------------------------------------------------------
    @app.get("/api/fonts")
    def api_fonts():
        return jsonify(fonts=_available_fonts())

    # ---- about -----------------------------------------------------------------
    @app.get("/api/about")
    def api_about():
        return jsonify(
            version=__version__,
            python=platform.python_version(),
            platform=platform.platform(),
            hostname=socket.gethostname(),
            runtime_dir=str(runtime.RUNTIME_DIR),
            free_memory=_free_memory(),
            printer_model="Brother VC-500W (ZINK color, raw XML+JPEG over :9100)",
        )

    return app


# --------------------------------------------------------------------------------
# helpers (module-level so they're testable)
# --------------------------------------------------------------------------------
def _status_dict(st) -> dict:
    return {
        "state": st.print_state,
        "stage": st.print_job_stage,
        "error": st.print_job_error,
        "remain_in": st.remain,
        "remain_cm": round(st.remain * 2.54, 1) if st.remain is not None else None,
        "cassette_type": st.cassette_type,
        "online": st.online,
        "capacity": st.capacity,
        "ready": st.ready,
    }


def _resolve_assets(dl: dict) -> dict:
    """Replace each image element's `src_id` with a real asset Path before render."""
    from PIL import Image  # noqa: F401  (Image used indirectly by compose)
    for el in dl.get("elements", []):
        if el.get("type") == "image" and el.get("src_id"):
            p = runtime.ASSETS_DIR / Path(el["src_id"]).name
            if p.exists():
                el["src"] = str(p)
    return dl


def _available_fonts() -> list[str]:
    """A small curated list of common fonts the server can actually load."""
    candidates = [
        "arial.ttf", "arialbd.ttf", "times.ttf", "cour.ttf", "verdana.ttf",
        "calibri.ttf", "segoeui.ttf", "DejaVuSans.ttf", "comic.ttf",
        "msyh.ttc", "simsun.ttc", "malgun.ttf", "YuGothR.ttc",  # CJK (deferred feature)
    ]
    out = []
    from PIL import ImageFont
    for name in candidates:
        try:
            ImageFont.truetype(name, 16)
            out.append(name)
        except OSError:
            continue
    return out or ["(default)"]


def _free_memory() -> str:
    try:
        import psutil  # optional
        return f"{psutil.virtual_memory().available // (1024*1024)} MB"
    except Exception:
        return "n/a (install psutil for memory stats)"


# ---- history file (JSONL) ----------------------------------------------------
def _read_history() -> list[dict]:
    if not runtime.HISTORY_FILE.exists():
        return []
    items = []
    for line in runtime.HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(items))  # newest first


def _write_history(items: list[dict]) -> None:
    runtime.ensure_runtime()
    # items come in newest-first; store oldest-first (append order)
    lines = [json.dumps(h, default=str) for h in reversed(items)]
    runtime.HISTORY_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _append_history(body: dict, jpeg: bytes, st) -> str:
    runtime.ensure_runtime()
    entry_id = hashlib.sha1(jpeg + runtime.now_iso().encode()).hexdigest()[:12]
    rec = {
        "id": entry_id,
        "timestamp": runtime.now_iso(),
        "name": body.get("name", ""),
        "media_mm": body.get("media_mm", 25),
        "bytes": len(jpeg),
        "remain_in": st.remain,
        "display_list": {k: v for k, v in body.items() if k != "src"},
    }
    with runtime.HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return entry_id


def _history_summary() -> tuple[int, str | None]:
    items = _read_history()
    return len(items), (items[0]["timestamp"] if items else None)


def main() -> None:
    """Entry point: `labler-web`. Runs the dev server on 0.0.0.0:5000."""
    import argparse
    ap = argparse.ArgumentParser(prog="labler-web", description="VC-500W label designer web app")
    ap.add_argument("-p", "--port", type=int, default=5000)
    ap.add_argument("-b", "--bind", default="0.0.0.0")
    ap.add_argument("-d", "--debug", action="store_true")
    args = ap.parse_args()
    create_app().run(host=args.bind, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
