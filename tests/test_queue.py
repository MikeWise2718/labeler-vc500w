"""Print-queue tests — serialization and position reporting.

The VC-500W accepts ONE :9100 connection at a time, and a client that releases
early wedges it until a power-cycle (CLAUDE.md GOTCHAs). With 2-4 people sharing
the printer, two guarantees matter:

  1. prints are serialized — never two at once, no matter the concurrency;
  2. a waiting request can be told its position, instead of hanging silently.

These use real threads: a queue bug is a concurrency bug, and a single-threaded
test would not see it.
"""

from __future__ import annotations

import threading
import time

import pytest

from labler.web.app import _PrintQueue


@pytest.fixture
def q():
    return _PrintQueue(threading.Lock())


# ---- serialization ---------------------------------------------------------

def test_only_one_holder_at_a_time(q):
    """The core guarantee: overlapping holds must not happen."""
    concurrent = 0
    max_concurrent = 0
    seen_lock = threading.Lock()
    errors = []

    def worker():
        nonlocal concurrent, max_concurrent
        try:
            t = q.take_ticket()
            with q.hold(t):
                with seen_lock:
                    concurrent += 1
                    max_concurrent = max(max_concurrent, concurrent)
                time.sleep(0.02)          # hold long enough to overlap if broken
                with seen_lock:
                    concurrent -= 1
        except Exception as e:            # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert max_concurrent == 1, f"printer held by {max_concurrent} threads at once"


def test_all_waiters_eventually_run(q):
    """No job is starved or lost."""
    done = []
    lock = threading.Lock()

    def worker(n):
        t = q.take_ticket()
        with q.hold(t):
            time.sleep(0.005)
            with lock:
                done.append(n)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert sorted(done) == list(range(10))


# ---- position reporting ----------------------------------------------------

def test_position_zero_while_holding(q):
    t = q.take_ticket()
    with q.hold(t):
        assert q.position(t) == 0


def test_position_counts_jobs_ahead(q):
    """A ticket taken while another prints reports how many are in front."""
    holder_in = threading.Event()
    release = threading.Event()

    def holder():
        t = q.take_ticket()
        with q.hold(t):
            holder_in.set()
            release.wait(timeout=5)

    th = threading.Thread(target=holder)
    th.start()
    assert holder_in.wait(timeout=5)

    second = q.take_ticket()          # queued behind the active holder
    third = q.take_ticket()
    assert q.position(second) == 1    # one ahead (the active print)
    assert q.position(third) == 2

    release.set()
    th.join(timeout=5)


def test_position_minus_one_when_unknown(q):
    assert q.position(999) == -1


def test_position_minus_one_after_completion(q):
    t = q.take_ticket()
    with q.hold(t):
        pass
    assert q.position(t) == -1


# ---- snapshot --------------------------------------------------------------

def test_snapshot_idle(q):
    s = q.snapshot()
    assert s["busy"] is False and s["waiting"] == 0


def test_snapshot_busy_while_holding(q):
    started = threading.Event()
    release = threading.Event()

    def holder():
        t = q.take_ticket()
        with q.hold(t):
            started.set()
            release.wait(timeout=5)

    th = threading.Thread(target=holder)
    th.start()
    assert started.wait(timeout=5)
    assert q.snapshot()["busy"] is True
    release.set()
    th.join(timeout=5)
    assert q.snapshot()["busy"] is False


# ---- failure handling ------------------------------------------------------

def test_exception_inside_hold_releases_the_printer(q):
    """A failed print must not strand the queue — this is the wedge scenario.

    If an exception left the lock held, every later print would block forever and
    the printer would look dead.
    """
    t = q.take_ticket()
    with pytest.raises(RuntimeError):
        with q.hold(t):
            raise RuntimeError("print blew up")

    # the printer must be free again
    assert q.snapshot()["busy"] is False
    t2 = q.take_ticket()
    with q.hold(t2):
        assert q.position(t2) == 0
    assert q.snapshot()["waiting"] == 0


def test_failed_job_leaves_no_ticket_behind(q):
    t = q.take_ticket()
    with pytest.raises(ValueError):
        with q.hold(t):
            raise ValueError("boom")
    assert q.position(t) == -1
    assert q.snapshot()["waiting"] == 0


def test_tickets_are_unique(q):
    tickets = {q.take_ticket() for _ in range(50)}
    assert len(tickets) == 50
