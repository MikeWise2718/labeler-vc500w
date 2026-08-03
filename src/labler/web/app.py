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

import base64
import hashlib
import json
import platform
import socket
import threading
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from .. import __version__, compose, power, protocol
from ..config import MEDIA, SUPPORTED_WIDTHS, media_for
from ..errors import LablerError
from ..render import _load_font  # for font availability probing
from . import runtime
from .runtime import WebSettings, log_event

# One printer at a time. Held across the whole status/print op.
#
# The VC-500W accepts a single :9100 connection, so every printer touch is
# serialized. With 2-4 people that is no longer invisible: a second print used to
# block on this lock until the socket timed out, with the browser showing nothing.
# _PrintQueue wraps the same lock so a waiting request can be TOLD where it is in
# line. The serialization guarantee is unchanged — only the visibility is new.
_printer_lock = threading.Lock()


class _PrintQueue:
    """Tracks who is waiting for the printer, so clients can show a position.

    Deliberately tiny: a counter for ticket numbers, a list of waiting tickets, and
    the lock itself. Not a job queue — requests still block in FIFO-ish order on
    _printer_lock; this only exposes how many are ahead of you.
    """

    def __init__(self, lock: threading.Lock):
        self._lock = lock                      # the real printer mutex
        self._guard = threading.Lock()         # guards the bookkeeping below
        self._next_ticket = 0
        self._waiting: list[int] = []          # tickets not yet holding the printer
        self._active: int | None = None        # ticket currently holding it

    def take_ticket(self) -> int:
        with self._guard:
            self._next_ticket += 1
            t = self._next_ticket
            self._waiting.append(t)
            return t

    def position(self, ticket: int) -> int:
        """0 = printing now, N = N jobs ahead, -1 = finished/unknown."""
        with self._guard:
            if self._active == ticket:
                return 0
            if ticket in self._waiting:
                # everyone ahead of us in the queue, plus whoever is printing
                ahead = self._waiting.index(ticket)
                return ahead + (1 if self._active is not None else 0)
            return -1

    def snapshot(self) -> dict:
        with self._guard:
            return {"waiting": len(self._waiting),
                    "busy": self._active is not None,
                    "active_ticket": self._active}

    @contextmanager
    def hold(self, ticket: int):
        """Acquire the printer for `ticket`, releasing the bookkeeping on exit."""
        self._lock.acquire()
        try:
            with self._guard:
                if ticket in self._waiting:
                    self._waiting.remove(ticket)
                self._active = ticket
            yield
        finally:
            with self._guard:
                if self._active == ticket:
                    self._active = None
                # defensive: a ticket must never be left waiting after its turn
                if ticket in self._waiting:
                    self._waiting.remove(ticket)
            self._lock.release()


_print_queue = _PrintQueue(_printer_lock)


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
            with _print_queue.hold(_print_queue.take_ticket()):
                st = protocol.get_status(host)
        except LablerError as e:
            return jsonify(ok=False, error=str(e), host=host), 502
        return jsonify(ok=True, host=host, **_status_dict(st))

    @app.get("/api/device")
    def api_device():
        host = _settings().host
        n_prints, last = _history_summary()
        try:
            with _print_queue.hold(_print_queue.take_ticket()):
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
            with _print_queue.hold(_print_queue.take_ticket()):
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
        # Take a ticket BEFORE blocking, so /api/queue can report our position while
        # we wait behind someone else's print.
        ticket = _print_queue.take_ticket()
        waited_from = _print_queue.position(ticket)
        try:
            with _print_queue.hold(ticket):
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
        # An id only — the design itself goes into the CLIENT's history (IndexedDB).
        entry = _new_entry_id(jpeg)
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
                       remain_after=remain_after, tape_used_in=used,
                       queued_behind=max(waited_from, 0), **_status_dict(st))

    # ---- assets --------------------------------------------------------------
    # REMOVED in v0.8.1. Bitmaps used to be POSTed here and stored under
    # ~/.labler/assets/, but an uploaded bitmap IS label content and the printer is
    # now shared. Images are inlined into the display list as data URIs instead and
    # never touch the server's disk. See specs/central-deployment.md.

    # ---- designs (saved display-lists) ----------------------------------------
    # ---- designs & history: REMOVED in v0.8.3 -----------------------------------
    # Designs and print history are label CONTENT and the printer is now shared, so
    # they live in the client's browser (static/store.js, IndexedDB) instead of in
    # ~/.labler/. The old endpoints (/api/designs*, /api/history*) are gone.
    #
    # The one exception below is a READ-ONLY, one-shot export so an existing
    # ~/.labler/ can be pulled into the browser on first load — without it, upgrading
    # silently loses every saved design. It writes nothing and is safe to call twice.

    @app.get("/api/migrate/export")
    def api_migrate_export():
        """One-shot dump of pre-0.8.3 server-side designs + history for the client
        to import into IndexedDB. Read-only; the files are left in place so a failed
        import can be retried."""
        designs = []
        if runtime.DESIGNS_DIR.exists():
            for ddir in sorted(runtime.DESIGNS_DIR.iterdir()):
                dj = ddir / "design.json"
                if not dj.is_file():
                    continue
                try:
                    dl = json.loads(dj.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                preview = None
                pj = ddir / "preview.png"
                if pj.exists():
                    try:
                        preview = ("data:image/png;base64,"
                                   + base64.b64encode(pj.read_bytes()).decode())
                    except OSError:
                        preview = None
                designs.append({
                    "id": dl.get("id") or ddir.name,
                    "name": dl.get("name") or ddir.name,
                    "updated": runtime.now_iso(),
                    "preview": preview,
                    "display_list": dl,
                })

        history = []
        for h in _read_history():
            thumb = None
            tp = runtime.HISTORY_DIR / f"{Path(str(h.get('id', ''))).name}.png"
            if tp.exists():
                try:
                    thumb = ("data:image/png;base64,"
                             + base64.b64encode(tp.read_bytes()).decode())
                except OSError:
                    thumb = None
            history.append({
                "id": h.get("id"),
                "timestamp": h.get("timestamp"),
                "name": h.get("name", ""),
                "media_mm": h.get("media_mm", 25),
                "ok": h.get("ok", True),
                "remain_before_in": h.get("remain_before_in"),
                "remain_after_in": h.get("remain_after_in"),
                "tape_used_in": h.get("tape_used_in"),
                "thumb": thumb,
                "display_list": h.get("display_list"),
            })

        log_event("migrate.export", "served legacy designs/history for client import",
                  count=len(designs) + len(history))
        return jsonify(ok=True, designs=designs, history=history)

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

    # ---- remote power-cycle (wedge recovery) ------------------------------------
    @app.post("/api/device/powercycle")
    def api_powercycle():
        """Cut and restore mains power to the printer via its Shelly outlet.

        DESTRUCTIVE: this yanks power from a device that may be mid-print. It is the
        documented and ONLY way out of a wedge (CLAUDE.md), but it is never automatic
        — the request must carry {"confirm": true}, which the UI only sends after the
        user agrees to a warning.
        """
        body = request.get_json(silent=True) or {}
        if body.get("confirm") is not True:
            return jsonify(ok=False,
                           error="power-cycle requires explicit confirmation"), 400
        s = _settings()
        if not s.shelly_host:
            return jsonify(ok=False,
                           error="no Shelly outlet configured (Settings → power control)",
                           hint="Set the Shelly host + outlet, or power-cycle by hand."), 400
        try:
            # Hold the printer lock so nobody starts a print into a dying printer.
            with _print_queue.hold(_print_queue.take_ticket()):
                result = power.power_cycle(s.shelly_host, s.shelly_outlet)
        except LablerError as e:
            log_event("device.powercycle_failed", str(e),
                      host=s.shelly_host, kind=type(e).__name__)
            return jsonify(ok=False, error=str(e)), 502
        log_event("device.powercycle", "printer power-cycled",
                  host=s.shelly_host, id=str(s.shelly_outlet))
        return jsonify(ok=True,
                       hint="Printer restarting — give it ~20 s before printing.",
                       **result)

    # ---- print queue -------------------------------------------------------------
    @app.get("/api/queue")
    def api_queue():
        """How busy the shared printer is right now.

        With several people on one printer, a browser that just hangs is
        indistinguishable from a broken one. This lets the UI say "someone else is
        printing" instead of spinning silently.
        """
        return jsonify(ok=True, **_print_queue.snapshot())

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


# ---- legacy history file (JSONL) ---------------------------------------------
# READ-ONLY as of v0.8.3. Nothing appends here any more — print history lives in
# the client's IndexedDB (static/store.js). This reader exists solely so
# /api/migrate/export can hand a pre-0.8.3 ~/.labler/history.jsonl to the browser
# once. _write_history() and _orientation() were removed with the write path.
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


def _new_entry_id(jpeg: bytes) -> str:
    """Stable-ish id for one print, handed to the client so its IndexedDB history
    entry and the server's stats row can be correlated.

    NB: the server no longer WRITES a history record. Storing the design would put
    label content (name, text, bitmaps) on a shared machine — that is exactly what
    v0.8.3 moved into the browser. Only the id and the tape statistics stay here.
    """
    return hashlib.sha1(jpeg + runtime.now_iso().encode()).hexdigest()[:12]


def _history_summary() -> tuple[int, str | None]:
    """Print count + last print time for the Device tab, from the STATS stream.

    Previously read history.jsonl; that file is legacy-only now (kept so the
    one-shot migration export can still find it) and no longer grows.
    """
    recs = runtime.read_stats()
    return len(recs), (recs[-1].get("timestamp") if recs else None)


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
