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
        """Body = display-list JSON -> PNG preview bytes — the EXACT image that prints.

        The preview is the same render as the JPEG sent to the printer (just PNG), so
        'what you see is what feeds out'. Orientation: 25mm tape width = image width
        (312px), length = image height. No separate view rotation.
        """
        dl = request.get_json(force=True)
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
        dl = request.get_json(force=True)
        try:
            return jsonify(ok=True, **compose.measure_display_list(dl))
        except Exception as e:
            return jsonify(ok=False, error=f"{type(e).__name__}: {e}"), 400

    @app.post("/api/print")
    def api_print():
        body = request.get_json(force=True)
        dl = body
        s = _settings()
        try:
            jpeg = compose.render_display_list(dl, fmt="JPEG")
        except Exception as e:
            log_event("render.error", str(e), kind=type(e).__name__)
            return jsonify(ok=False, error=f"{type(e).__name__}: {e}"), 400
        mode = body.get("mode", s.mode)
        cut = body.get("cut", s.cut)
        media_mm = body.get("media_mm")
        remain_before = None      # set inside the lock; needed by the failure path too
        try:
            with _printer_lock:
                # Capture tape remaining BEFORE the print, then print, then read it
                # AGAIN. The before/after delta is the TRUE tape consumed (hardware
                # truth) — more reliable than the pixel estimate, which the printer's
                # autofit can scale unpredictably for landscape images.
                try:
                    remain_before = protocol.get_status(s.host).remain
                except LablerError:
                    remain_before = None
                st = protocol.print_jpeg(s.host, jpeg, mode=mode, cut=cut)
        except LablerError as e:
            log_event("print.failed", str(e), host=s.host, kind=type(e).__name__)
            # A failed attempt still belongs in the shared tape record — a jam that
            # ate tape is exactly what someone reading the roll burn-down needs.
            runtime.record_print_stats(
                host=s.host, media_mm=media_mm, mode=mode, cut=cut, ok=False,
                error_kind=type(e).__name__, remain_before_in=remain_before,
                jpeg_bytes=len(jpeg))
            return jsonify(ok=False, error=str(e)), 502
        ok = st.print_job_error in (None, "NONE")
        remain_after = st.remain
        entry = _append_history(body, jpeg, st, remain_before=remain_before)
        log_event("print.done", "printed", host=s.host, ok=ok,
                  state=st.print_state, remain=st.remain, entry=entry,
                  remain_before=remain_before, remain_after=remain_after)
        used = (round(remain_before - remain_after, 2)
                if remain_before is not None and remain_after is not None else None)
        runtime.record_print_stats(
            host=s.host, media_mm=media_mm, mode=mode, cut=cut, ok=ok,
            error_kind=None if ok else (st.print_job_error or "UNKNOWN"),
            remain_before_in=remain_before, remain_after_in=remain_after,
            tape_used_in=used, jpeg_bytes=len(jpeg))
        return jsonify(ok=ok, entry=entry, remain_before=remain_before,
                       remain_after=remain_after, tape_used_in=used, **_status_dict(st))

    # ---- assets --------------------------------------------------------------
    # REMOVED in v0.8.1. Bitmaps used to be POSTed here and stored under
    # ~/.labler/assets/, but an uploaded bitmap IS label content and the printer is
    # now shared. Images are inlined into the display list as data URIs instead and
    # never touch the server's disk. See specs/central-deployment.md.

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
            png = compose.render_display_list(dict(dl), fmt="PNG")
            (ddir / "preview.png").write_bytes(png)
        except (ValueError, OSError):
            pass
        # NB: the design NAME is label content and is deliberately not logged —
        # runtime.LOG_FIELD_ALLOWLIST would drop it anyway. Log the id only.
        log_event("design.save", "saved design", id=did)
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
        # Backfill orientation/length for entries saved before those fields existed,
        # using their stored display_list. Cheap; keeps old prints fully shown.
        items = _read_history()
        for h in items:
            # Backfill orientation/estimate for old entries from their display_list.
            if h.get("orientation") is None and h.get("display_list"):
                try:
                    d = compose.measure_display_list(dict(h["display_list"]))
                    h["est_width_px"] = h.get("est_width_px") or d["width_px"]
                    h["est_length_px"] = h.get("est_length_px") or d["length_px"]
                    h.setdefault("est_length_cm", d["length_cm"])
                    h["orientation"] = _orientation(d["width_px"], d["length_px"])
                except Exception:
                    pass
            # Back-compat: older entries stored length under different keys.
            if h.get("tape_used_cm") is None and h.get("tape_used_in") is not None:
                h["tape_used_cm"] = round(h["tape_used_in"] * 2.54, 1)
            h["thumb"] = f"/api/history/{h['id']}/thumb.png"
        return jsonify(history=items)

    @app.get("/api/history/<entry>/thumb.png")
    def api_history_thumb(entry):
        # Prefer the saved thumbnail; fall back to rendering the stored display_list
        # for old entries that predate thumbnail saving. Return bytes (not send_file)
        # so no file handle lingers — on Windows an open handle blocks the later
        # delete-unlink of the same thumbnail.
        from flask import Response
        path = runtime.HISTORY_DIR / f"{Path(entry).name}.png"
        if path.exists():
            return Response(path.read_bytes(), mimetype="image/png")
        for h in _read_history():
            if h.get("id") == entry and h.get("display_list"):
                try:
                    png = compose.render_display_list(
                        dict(h["display_list"]), fmt="PNG")
                    from flask import Response
                    return Response(png, mimetype="image/png")
                except Exception:
                    break
        abort(404)

    @app.delete("/api/history/<entry>")
    def api_delete_history(entry):
        items = [h for h in _read_history() if h.get("id") != entry]
        _write_history(items)
        thumb = runtime.HISTORY_DIR / f"{Path(entry).name}.png"
        try:
            thumb.unlink(missing_ok=True)
        except OSError:
            pass
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
        from ..render import FONT_FILE_TO_FAMILY
        return jsonify(fonts=_available_fonts(), legacy=FONT_FILE_TO_FAMILY)

    # ---- shared tape statistics -------------------------------------------------
    @app.get("/api/stats")
    def api_stats():
        """Shared roll accounting: how much tape has gone, by whom-agnostic day.

        Deliberately contains NO label content — this is the one dataset that is
        meant to be shared between everyone using the printer. See
        specs/central-deployment.md.
        """
        recs = runtime.read_stats()
        return jsonify(ok=True, **runtime.summarise_stats(recs))

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


# _resolve_assets() was REMOVED in v0.8.1 along with /api/assets. Image elements now
# carry their own `src` data URI, which compose._resolve_image decodes directly, so
# there is nothing server-side to resolve. See specs/central-deployment.md.


def _available_fonts() -> list[dict]:
    """Font FAMILIES the server can load, each with which styles are available.

    Returns [{name, has_bold, has_italic}, ...] for every family in
    render.FONT_FAMILIES whose regular file loads. The UI shows the family name
    and enables Bold/Italic toggles per `has_*`. Bold/italic resolution happens
    server-side in render._load_font(family, bold=, italic=).
    """
    from PIL import ImageFont

    from ..render import FONT_FAMILIES

    def loads(name: str | None) -> bool:
        if not name:
            return False
        try:
            ImageFont.truetype(name, 16)
            return True
        except OSError:
            return False

    out = []
    for fam, files in FONT_FAMILIES.items():
        if not loads(files.get("r")):
            continue  # family's regular face isn't installed -> skip the family
        out.append({
            "name": fam,
            "has_bold": loads(files.get("b")) or loads(files.get("bi")),
            "has_italic": loads(files.get("i")) or loads(files.get("bi")),
        })
    return out or [{"name": "(default)", "has_bold": False, "has_italic": False}]


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


def _orientation(width_px: int, length_px: int) -> str:
    """Portrait = taller than wide (length > across-tape); landscape = wider."""
    if length_px > width_px:
        return "portrait"
    if length_px < width_px:
        return "landscape"
    return "square"


def _append_history(body: dict, jpeg: bytes, st, *, remain_before=None) -> str:
    runtime.ensure_runtime()
    entry_id = hashlib.sha1(jpeg + runtime.now_iso().encode()).hexdigest()[:12]
    dl = {k: v for k, v in body.items() if k != "src"}
    # Pixel estimate of the design size (best-effort; the printer's autofit may scale
    # it differently, so the hardware before/after remain is the authoritative used).
    try:
        dims = compose.measure_display_list(dict(dl))
    except Exception:
        dims = {}
    # Save a thumbnail PNG so the History tab can show what was printed.
    try:
        (runtime.HISTORY_DIR / f"{entry_id}.png").write_bytes(jpeg)
    except OSError:
        pass
    remain_after = st.remain
    tape_used_in = (round(remain_before - remain_after, 2)
                    if remain_before is not None and remain_after is not None else None)
    rec = {
        "id": entry_id,
        "timestamp": runtime.now_iso(),
        "name": body.get("name", ""),
        "media_mm": body.get("media_mm", 25),
        "bytes": len(jpeg),
        # tape stats (hardware truth)
        "remain_before_in": remain_before,
        "remain_after_in": remain_after,
        "tape_used_in": tape_used_in,
        "tape_used_cm": round(tape_used_in * 2.54, 1) if tape_used_in is not None else None,
        "remain_in": remain_after,                     # back-compat alias
        # pixel estimate of the design (not necessarily what fed out)
        "est_width_px": dims.get("width_px"),
        "est_length_px": dims.get("length_px"),
        "est_length_cm": dims.get("length_cm"),
        "orientation": _orientation(dims.get("width_px", 0), dims.get("length_px", 0)) if dims else None,
        "display_list": dl,
    }
    with runtime.HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return entry_id


def _history_summary() -> tuple[int, str | None]:
    items = _read_history()
    return len(items), (items[0]["timestamp"] if items else None)


class _LablerRequestHandler:
    """Mixin for werkzeug's WSGIRequestHandler: colorized, labler-tagged access log.

    Replaces the plain ``192.168.25.5 - - [..] "GET /api/status" 200 -`` lines with a
    colored line prefixed by ``labler`` so it's obvious which app is talking. Status
    code is colored by class (2xx green, 3xx cyan, 4xx yellow, 5xx red); method and
    path are dimmed for the noisy polling routes (/api/status, /api/ping).
    """

    # quiet routes that poll constantly — dim them so real traffic stands out
    _QUIET = ("/api/status", "/api/ping")

    def log_request(self, code="-", size="-"):  # noqa: D401 (werkzeug signature)
        from rich.console import Console

        console = getattr(self.__class__, "_console", None)
        if console is None:
            console = Console(stderr=True)
            self.__class__._console = console

        try:
            code_i = int(code)
        except (TypeError, ValueError):
            code_i = 0
        if code_i >= 500:
            code_color = "bold red"
        elif code_i >= 400:
            code_color = "yellow"
        elif code_i >= 300:
            code_color = "cyan"
        else:
            code_color = "green"

        line = self.requestline  # e.g. 'GET /api/status HTTP/1.1'
        path = line.split(" ")[1] if " " in line else line
        quiet = any(path.startswith(q) for q in self._QUIET)
        req_style = "dim" if quiet else "white"
        client = self.address_string()

        console.print(
            f"[bold magenta]labler[/] "
            f"[dim]{client}[/] "
            f"[{req_style}]{line}[/] "
            f"[{code_color}]{code}[/]"
        )

    def log(self, type, message, *args):  # noqa: A002 (werkzeug signature)
        # Route werkzeug's other logs (errors, warnings) through rich too.
        from rich.console import Console

        console = getattr(self.__class__, "_console", None)
        if console is None:
            console = Console(stderr=True)
            self.__class__._console = console
        color = "red" if type == "error" else "yellow" if type == "warning" else "dim"
        console.print(f"[bold magenta]labler[/] [{color}]{message % args}[/]")


def _make_request_handler():
    """Build a WSGIRequestHandler subclass with our colorized logging mixed in."""
    from werkzeug.serving import WSGIRequestHandler

    class LablerWSGIRequestHandler(_LablerRequestHandler, WSGIRequestHandler):
        pass

    return LablerWSGIRequestHandler


def main() -> None:
    """Entry point: `labler-web`. Runs the dev server on 0.0.0.0:5000."""
    import argparse

    from rich.console import Console

    ap = argparse.ArgumentParser(prog="labler-web", description="VC-500W label designer web app")
    ap.add_argument("-p", "--port", type=int, default=5000)
    ap.add_argument("-b", "--bind", default="0.0.0.0")
    ap.add_argument("-d", "--debug", action="store_true")
    args = ap.parse_args()

    console = Console(stderr=True)
    console.print(
        f"[bold magenta]labler[/] [green]VC-500W label designer[/] "
        f"[dim]v{__version__}[/] → [cyan]http://{args.bind}:{args.port}[/]"
    )
    create_app().run(
        host=args.bind,
        port=args.port,
        debug=args.debug,
        request_handler=_make_request_handler(),
    )


if __name__ == "__main__":
    main()
