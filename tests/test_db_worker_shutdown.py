"""Database-polling workers stop before the gateway starts closing anything else."""

import signal
import threading
from types import SimpleNamespace

import pytest

from commonplace import holds
from commonplace.chat import handoff, settings, store, worker
from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway


@pytest.fixture
def live(tmp_path):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    holds.ensure_schema(db)
    for module in (settings, store, handoff):
        module.ensure_schema(db)
    gateway = SimpleNamespace(db=db)
    gateway.hold_worker = holds.HoldWorker(gateway)
    gateway.chat_worker = worker.FrontWorker(gateway)
    # install() registers the same callbacks in production.
    db.add_pre_close_callback(gateway.hold_worker.stop)
    db.add_pre_close_callback(gateway.chat_worker.stop)
    statements = []
    # The trace hook is installed after both workers are running, as late SQL would be.
    db._conn.set_trace_callback(statements.append)
    yield gateway, statements
    db.close()


def test_db_close_stops_live_hold_worker_before_sqlite_closes(live):
    gateway, statements = live
    thread = gateway.hold_worker.thread
    assert thread.is_alive()
    gateway.db.close()
    assert not thread.is_alive()
    seen = len(statements)
    gateway.hold_worker._wake.set()
    thread.join(timeout=5)
    assert len(statements) == seen
    assert not any("answer_holds" in text for text in statements[seen:])


def test_serve_stops_hold_and_chat_workers_before_server_close(live, monkeypatch):
    gateway, statements = live
    hold_thread, chat_thread = gateway.hold_worker.thread, gateway.chat_worker.thread
    state = {}

    class Server:
        server_address = ("127.0.0.1", 1)

        def serve_forever(self):
            raise RuntimeError("test shutdown")

        def server_close(self):
            state["hold_alive"] = hold_thread.is_alive()
            state["chat_alive"] = chat_thread.is_alive()
            # A due hold would settle here; the worker must already be gone.
            gateway.hold_worker._wake.set()
            hold_thread.join(timeout=0.2)
            state["sql_after_close_started"] = [s for s in statements if "answer_holds" in s]

    class Reaper:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", Reaper)
    monkeypatch.setattr(signal, "signal", lambda *_args: None)
    real = Gateway.__new__(Gateway)
    real.build_server = lambda *_args: Server()
    real.db = gateway.db
    real.hold_worker, real.chat_worker = gateway.hold_worker, gateway.chat_worker
    real.handoff = None
    real.supervisor = SimpleNamespace(stop_idle=lambda: None, close=lambda: None)
    statements.clear()
    with pytest.raises(RuntimeError, match="test shutdown"):
        real.serve("127.0.0.1", 0)
    assert state["hold_alive"] is False
    assert state["chat_alive"] is False
    assert state["sql_after_close_started"] == []
