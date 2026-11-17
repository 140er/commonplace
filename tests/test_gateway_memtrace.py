"""Operator-only allocation tracing and cheap gateway process counters."""

import logging
import threading
from types import SimpleNamespace

import pytest

from commonplace import memtrace


def stat(index, size):
    frame = SimpleNamespace(filename=f"/private/user-content/allocation{index}.py", lineno=index)
    return SimpleNamespace(size_diff=size, count_diff=index, traceback=[frame])


class Snapshot:
    def __init__(self, stats=()):
        self.stats = stats
        self.compared = None

    def compare_to(self, previous, key):
        assert key == "lineno"
        self.compared = previous
        return self.stats


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes"])
def test_trace_is_off_without_the_explicit_switch(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("COMMONPLACE_MEMTRACE", raising=False)
    else:
        monkeypatch.setenv("COMMONPLACE_MEMTRACE", value)

    def forbidden(*args):
        pytest.fail("Tracing must not even inspect the heap while disabled")

    monkeypatch.setattr(memtrace, "MemoryTrace", forbidden)
    assert memtrace.start() is None


def test_rotates_snapshots_and_logs_only_ten_positive_sites(monkeypatch, caplog):
    trace = memtrace.MemoryTrace()
    first = Snapshot()
    second = Snapshot([stat(0, -1)] + [stat(i, i * 1024) for i in range(1, 15)])
    third = Snapshot()
    snapshots = iter([first, second, third])
    monkeypatch.setattr(memtrace.tracemalloc, "take_snapshot", lambda: next(snapshots))
    caplog.set_level(logging.WARNING, logger=memtrace.__name__)
    trace.sample()
    assert not caplog.records
    trace.sample()
    assert second.compared is first
    assert trace.previous is second
    assert len(caplog.records) == 10
    assert "allocation1.py:1 size_bytes=1024 count=1" in caplog.text
    assert "allocation11.py" not in caplog.text
    assert "/private/" not in caplog.text and "user-content" not in caplog.text
    trace.sample()
    assert third.compared is second and trace.previous is third


@pytest.mark.parametrize("existing", [False, True])
def test_background_trace_lifetime_and_existing_tracer(monkeypatch, existing):
    starts, stops, waits = [], [], []
    monkeypatch.setattr(memtrace.tracemalloc, "is_tracing", lambda: existing)
    monkeypatch.setattr(memtrace.tracemalloc, "start", starts.append)
    monkeypatch.setattr(memtrace.tracemalloc, "stop", lambda: stops.append(True))
    monkeypatch.setattr(memtrace.tracemalloc, "take_snapshot", lambda: Snapshot())
    trace = memtrace.MemoryTrace()
    monkeypatch.setattr(trace.stop, "wait", lambda seconds: waits.append(seconds) or True)
    trace.run()
    assert starts == ([] if existing else [5])
    assert stops == ([] if existing else [True])
    assert waits == [600]
    assert trace.previous is None


def test_failed_sample_retries_without_logging_exception_data(monkeypatch, caplog):
    monkeypatch.setattr(memtrace.tracemalloc, "is_tracing", lambda: True)
    trace = memtrace.MemoryTrace()
    calls = []

    def snapshot():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("request-content-secret")
        return Snapshot()

    monkeypatch.setattr(memtrace.tracemalloc, "take_snapshot", snapshot)
    monkeypatch.setattr(trace.stop, "wait", lambda seconds: len(calls) == 2)
    trace.run()
    assert len(calls) == 2
    assert "sample unavailable" in caplog.text and "request-content-secret" not in caplog.text
    assert trace.previous is None


def test_start_and_close_join_the_background_thread(monkeypatch):
    sampled = threading.Event()
    monkeypatch.setenv("COMMONPLACE_MEMTRACE", "1")
    monkeypatch.setattr(memtrace.tracemalloc, "is_tracing", lambda: True)
    monkeypatch.setattr(memtrace.tracemalloc, "take_snapshot", lambda: sampled.set() or Snapshot())
    trace = memtrace.start()
    try:
        assert sampled.wait(2)
    finally:
        trace.close()
    assert not trace.thread.is_alive() and trace.previous is None


def test_unavailable_counters_leave_other_fields_readable(monkeypatch):
    monkeypatch.setattr(memtrace.threading, "active_count", lambda: 7)

    def denied(*args):
        raise PermissionError("private target")

    monkeypatch.setattr(memtrace.os, "scandir", denied)
    assert memtrace.process_counts() == {"thread_count": 7, "open_fd_count": None}


def test_gateway_closes_trace_when_http_loop_fails(monkeypatch):
    from commonplace.gateway import Gateway
    from commonplace.rules import checks

    closed = []
    trace = SimpleNamespace(close=lambda: closed.append("trace"))
    monkeypatch.setattr(memtrace, "start", lambda: trace)
    monkeypatch.setattr(checks, "deploy", lambda gateway: None)

    def crash():
        raise RuntimeError("server stopped")

    server = SimpleNamespace(
        serve_forever=crash,
        server_address=("127.0.0.1", 0),
        server_close=lambda: closed.append("http"),
    )
    gateway = SimpleNamespace(
        supervisor=SimpleNamespace(close=lambda: closed.append("workers")),
        build_server=lambda host, port: server,
        _stop_db_workers=lambda: closed.append("database workers"),
    )
    # serve() installs signals only on the main thread; preserve the test runner's handler.
    import signal

    monkeypatch.setattr(signal, "signal", lambda *args: None)
    with pytest.raises(RuntimeError, match="server stopped"):
        Gateway.serve(gateway, "127.0.0.1", 0)
    assert closed == ["trace", "database workers", "http", "workers"]
