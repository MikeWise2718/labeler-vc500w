"""Typed exceptions for the labler core.

Front-ends (CLI, Flask) catch these to give the user actionable messages instead
of raw socket/IO errors.
"""


class LablerError(Exception):
    """Base class for all labler errors."""


class ConnectionBusy(LablerError):
    """Could not open a TCP connection to the printer's :9100 control port.

    The VC-500W accepts only ONE connection at a time. The usual cause is that
    Brother's setup/desktop app (or another labler run, or a stale socket) is
    holding the slot — in that state connects time out even though the printer
    still answers ICMP ping. See CLAUDE.md "single connection slot" gotcha.
    """


class PrinterError(LablerError):
    """The printer reported an error (non-zero code or print_job_error != NONE)."""


class MediaError(LablerError):
    """A media problem: no cassette, wrong/empty tape, or requested width mismatch."""
