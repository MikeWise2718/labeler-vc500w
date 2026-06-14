"""Command-line interface for labler.

Thin layer: parse args, call the core, render results with rich. Full command
behaviour lands in Phase 3; this scaffold wires up argparse, --version, and the
subcommand skeleton so the `labler` entry point is runnable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from rich_argparse import RichHelpFormatter

from . import __version__, protocol, render
from .config import SUPPORTED_WIDTHS, Settings
from .errors import LablerError

console = Console()


def _parse_crop(s: str | None):
    if not s:
        return None
    try:
        x, y, w, h = (int(v) for v in s.split(","))
        return (x, y, w, h)
    except ValueError:
        raise SystemExit("--crop must be 'x,y,w,h' (four integers)")


def build_parser() -> argparse.ArgumentParser:
    s = Settings.load()
    p = argparse.ArgumentParser(
        prog="labler",
        description="Print labels directly to a Brother VC-500W over the LAN.",
        formatter_class=RichHelpFormatter,
    )
    p.add_argument("-V", "--version", action="version", version=f"labler {__version__}")

    # Common flags accepted both before and after the subcommand. The subparser copies
    # use SUPPRESS so that omitting them after the command doesn't clobber a value given
    # before it (and vice-versa); top-level holds the real defaults.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-H", "--host", default=argparse.SUPPRESS, help="printer IP/hostname")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="show protocol/progress detail")
    p.add_argument("-H", "--host", default=s.host, help="printer IP/hostname")
    p.add_argument("-v", "--verbose", action="store_true", help="show protocol/progress detail")

    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("status", help="query the printer and show its state", parents=[common])

    def add_print_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("-mw", "--media-width", type=int, default=s.media_width,
                        choices=SUPPORTED_WIDTHS, help="tape width in mm")
        sp.add_argument("-m", "--mode", default=s.mode, choices=("vivid", "normal"))
        sp.add_argument("-ct", "--cut", default=s.cut, choices=("none", "half", "full"))
        sp.add_argument("-r", "--rotate", type=int, default=0, choices=(0, 90, 180, 270))
        sp.add_argument("-n", "--dry-run", action="store_true", help="render only, do not print")
        sp.add_argument("-o", "--output", help="save rendered JPEG to this path (implies --dry-run)")

    pi = sub.add_parser("print-image", help="print an image file", parents=[common])
    pi.add_argument("file", help="image file to print")
    pi.add_argument("-cr", "--crop", help="crop box x,y,w,h")
    add_print_common(pi)

    pt = sub.add_parser("print-text", help="render text and print it", parents=[common])
    pt.add_argument("text", help="text to print")
    add_print_common(pt)

    pq = sub.add_parser("print-qr", help="render a QR code and print it", parents=[common])
    pq.add_argument("data", help="data to encode in the QR")
    add_print_common(pq)

    return p


def _cmd_status(args) -> int:
    st = protocol.get_status(args.host)
    table = Table(title=f"VC-500W @ {args.host}", show_header=False)
    table.add_row("State", str(st.print_state))
    table.add_row("Stage", str(st.print_job_stage))
    table.add_row("Error", str(st.print_job_error))
    table.add_row("Tape remaining", str(st.remain))
    table.add_row("Cassette type", str(st.cassette_type))
    table.add_row("Online", str(st.online))
    table.add_row("Power capacity", f"{st.capacity}%" if st.capacity is not None else "?")
    ready = "[green]READY[/]" if st.ready else "[yellow]not ready[/]"
    table.add_row("Ready?", ready)
    console.print(table)
    return 0


def _render_for(args) -> bytes:
    if args.command == "print-image":
        return render.render_image(args.file, media_mm=args.media_width,
                                   rotate=args.rotate, crop=_parse_crop(args.crop))
    if args.command == "print-text":
        return render.render_text(args.text, media_mm=args.media_width, rotate=args.rotate)
    if args.command == "print-qr":
        return render.render_qr(args.data, media_mm=args.media_width, rotate=args.rotate)
    raise AssertionError(args.command)


def _cmd_print(args) -> int:
    jpeg = _render_for(args)
    dry = args.dry_run or args.output
    if args.output:
        Path(args.output).write_bytes(jpeg)
        console.print(f"[green]wrote[/] {args.output} ({len(jpeg)} bytes)")
    if dry:
        if not args.output:
            console.print(f"[yellow]dry-run[/]: rendered {len(jpeg)} bytes, not printing")
        return 0

    def progress(stage: str) -> None:
        if args.verbose:
            console.print(f"  [dim]{stage}[/]")

    with console.status(f"Printing to {args.host}…"):
        st = protocol.print_jpeg(args.host, jpeg, mode=args.mode, cut=args.cut,
                                 on_progress=progress)
    if st.ready and (st.print_job_error in (None, "NONE")):
        console.print("[green]✓ printed[/]")
    else:
        console.print(f"[yellow]finished with state={st.print_state} error={st.print_job_error}[/]")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        if args.command == "status":
            return _cmd_status(args)
        return _cmd_print(args)
    except LablerError as e:
        console.print(f"[red]error:[/] {e}")
        return 1
    except FileNotFoundError as e:
        console.print(f"[red]error:[/] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
