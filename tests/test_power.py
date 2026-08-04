"""Tests for remote power-cycle recovery (src/labeler/power.py).

A wedged VC-500W only clears with a power-cycle (CLAUDE.md GOTCHAs). Once the
printer lives in the basement next to munchlax, nobody is standing there to pull
the plug, so recovery is an API call through a Shelly outlet.

Nothing here touches a real outlet — every RPC is monkeypatched. Cutting mains
power to a printer mid-print is destructive, so the safety rails (explicit
confirmation, wedge fingerprint needing >= 2 samples) are tested as carefully as
the happy path.
"""

from __future__ import annotations

import pytest

from labeler import power
from labeler.errors import LabelerError
from labeler.status import Status


class _FakeShelly:
    """Records RPC calls instead of making them."""

    def __init__(self, output=True, fail=False):
        self.calls: list[str] = []
        self.output = output
        self.fail = fail

    def __call__(self, host, method, params=""):
        if self.fail:
            raise LabelerError(f"Shelly at {host} unreachable: boom")
        self.calls.append(f"{method}?{params}" if params else method)
        if method == "Switch.GetStatus":
            return '{"id":0,"output":%s,"apower":1.2}' % ("true" if self.output else "false")
        return '{"was_on":true}'


@pytest.fixture
def shelly(monkeypatch):
    fake = _FakeShelly()
    monkeypatch.setattr(power, "_rpc", fake)
    return fake


# ---- outlet state ----------------------------------------------------------

def test_outlet_state_reads_output_true(shelly):
    shelly.output = True
    assert power.outlet_state("1.2.3.4", 0) is True


def test_outlet_state_reads_output_false(shelly):
    shelly.output = False
    assert power.outlet_state("1.2.3.4", 0) is False


def test_set_outlet_sends_expected_rpc(shelly):
    power.set_outlet("1.2.3.4", 2, True)
    assert "Switch.Set?id=2&on=true" in shelly.calls
    power.set_outlet("1.2.3.4", 2, False)
    assert "Switch.Set?id=2&on=false" in shelly.calls


# ---- power cycle -----------------------------------------------------------

def test_power_cycle_turns_off_then_on(shelly):
    slept = []
    result = power.power_cycle("1.2.3.4", 1, sleep=slept.append)
    # order matters: off, wait, on
    sets = [c for c in shelly.calls if c.startswith("Switch.Set")]
    assert sets == ["Switch.Set?id=1&on=false", "Switch.Set?id=1&on=true"]
    assert slept == [power.OFF_SECONDS]
    assert result["outlet"] == 1


def test_power_cycle_waits_long_enough_to_drain(shelly):
    """A brief blip can leave the printer in the same wedged state."""
    slept = []
    power.power_cycle("1.2.3.4", 0, sleep=slept.append)
    assert slept[0] >= 5, "off-time too short to reliably reset the controller"


def test_power_cycle_reports_prior_state(shelly):
    shelly.output = False
    assert power.power_cycle("1.2.3.4", 0, sleep=lambda s: None)["was_on"] is False


def test_power_cycle_raises_when_shelly_unreachable(monkeypatch):
    """A failed power-cycle must surface, not pretend to have worked — the printer
    is just as wedged as before."""
    monkeypatch.setattr(power, "_rpc", _FakeShelly(fail=True))
    with pytest.raises(LabelerError, match="unreachable"):
        power.power_cycle("1.2.3.4", 0, sleep=lambda s: None)


# ---- wedge fingerprint -----------------------------------------------------

def _st(state, stage, remain):
    return Status(raw={}, print_state=state, print_job_stage=stage,
                  print_job_error="NONE", remain=remain, cassette_type=1)


def test_wedge_needs_at_least_two_samples():
    """One sample cannot distinguish a wedge from a healthy print."""
    assert power.looks_wedged([_st("BUSY", "PRINTING", 10.0)]) is False
    assert power.looks_wedged([]) is False


def test_frozen_busy_with_static_remain_is_wedged():
    samples = [_st("BUSY", "PRINTING", 10.0)] * 3
    assert power.looks_wedged(samples) is True


def test_advancing_remain_is_a_healthy_print():
    """Tape is moving — that is a real print, do NOT cut its power."""
    samples = [_st("BUSY", "PRINTING", 10.0), _st("BUSY", "PRINTING", 9.5)]
    assert power.looks_wedged(samples) is False


def test_advancing_stage_is_a_healthy_print():
    samples = [_st("BUSY", "PREHEAT", 10.0), _st("BUSY", "PRINTING", 10.0)]
    assert power.looks_wedged(samples) is False


def test_idle_printer_is_not_wedged():
    samples = [_st("IDLE", "READY FOR PRINT", 10.0)] * 2
    assert power.looks_wedged(samples) is False


def test_missing_remain_is_not_treated_as_wedged():
    """Unknown tape figures must not trigger a destructive action."""
    samples = [_st("BUSY", "PRINTING", None), _st("BUSY", "PRINTING", None)]
    assert power.looks_wedged(samples) is False
