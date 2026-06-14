"""Command-line interface for labler.

Thin layer: parse args, call the core, render results with rich. Full command
behaviour lands in Phase 3; this scaffold wires up argparse, --version, and the
subcommand skeleton so the `labler` entry point is runnable.
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich_argparse import RichHelpFormatter

from . import __version__
from .config import SUPPORTED_WIDTHS, Settings

console = Console()


def build_parser() -> argparse.ArgumentParser:
    s = Settings.load()
    p = argparse.ArgumentParser(
        prog="labler",
        description="Print labels directly to a Brother VC-500W over the LAN.",
        formatter_class=RichHelpFormatter,
    )
    p.add_argument("-V", "--version", action="version", version=f"labler {__version__}")
    p.add_argument("-H", "--host", default=s.host, help="printer IP/hostname")
    p.add_argument("-v", "--verbose", action="store_true", help="show protocol/progress detail")

    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("status", help="query the printer and show its state")

    def add_print_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("-mw", "--media-width", type=int, default=s.media_width,
                        choices=SUPPORTED_WIDTHS, help="tape width in mm")
        sp.add_argument("-m", "--mode", default=s.mode, choices=("vivid", "normal"))
        sp.add_argument("-ct", "--cut", default=s.cut, choices=("none", "half", "full"))
        sp.add_argument("-r", "--rotate", type=int, default=0, choices=(0, 90, 180, 270))
        sp.add_argument("-n", "--dry-run", action="store_true", help="render only, do not print")
        sp.add_argument("-o", "--output", help="save rendered JPEG to this path (implies --dry-run)")

    pi = sub.add_parser("print-image", help="print an image file")
    pi.add_argument("file", help="image file to print")
    pi.add_argument("-cr", "--crop", help="crop box x,y,w,h")
    add_print_common(pi)

    pt = sub.add_parser("print-text", help="render text and print it")
    pt.add_argument("text", help="text to print")
    add_print_common(pt)

    pq = sub.add_parser("print-qr", help="render a QR code and print it")
    pq.add_argument("data", help="data to encode in the QR")
    add_print_common(pq)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Commands are implemented in Phase 2/3. For now, acknowledge and exit.
    console.print(f"[yellow]'{args.command}' not implemented yet[/] (host={args.host})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
