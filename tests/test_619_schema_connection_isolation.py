"""Schema checks cannot borrow a background transaction on the hub connection."""

import threading
from concurrent.futures import ThreadPoolExecutor

from test_commonplace_plans import (
    test_plan_schema_rejects_unknown_plans_and_users as check_plan_schema,
)

from tests._commonplace_helpers import hub_fixture as hub_fixture


def test_schema_checks_serialize_with_background_transaction(hub, monkeypatch):
    db = hub.db
    lock = db._lock
    locked = threading.Event()
    attempted = threading.Event()
    checker_id = None
    inserts = []

    class ObservedLock:
        def __enter__(self):
            if threading.get_ident() == checker_id:
                attempted.set()
            return lock.__enter__()

        def __exit__(self, *args):
            return lock.__exit__(*args)

        def __getattr__(self, name):
            return getattr(lock, name)

    def observe(sql):
        if threading.get_ident() == checker_id and sql.startswith("INSERT INTO account_plans"):
            inserts.append((sql, lock._is_owned()))
            attempted.set()

    def background():
        with db._tx() as conn:
            conn.execute("INSERT INTO meta VALUES ('schema-619-background', 'retained')")
            locked.set()
            assert attempted.wait(10), "schema check never attempted the shared connection"

    def check():
        nonlocal checker_id
        checker_id = threading.get_ident()
        try:
            check_plan_schema(hub)
        finally:
            attempted.set()

    with monkeypatch.context() as patch:
        patch.setattr(db, "_lock", ObservedLock())
        with lock:
            db._conn.set_trace_callback(observe)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                writer = pool.submit(background)
                assert locked.wait(10), "background transaction did not start"
                schema = pool.submit(check)
                try:
                    schema.result()
                finally:
                    attempted.set()
                    writer.result()
        finally:
            with lock:
                db._conn.set_trace_callback(None)

    assert len(inserts) == 2, "both original invalid INSERTs must execute"
    assert all(owned for _, owned in inserts), (
        "schema SQL used the background connection without owning its lock"
    )
    with lock:
        assert (
            db._conn.execute("SELECT value FROM meta WHERE key='schema-619-background'").fetchone()[
                0
            ]
            == "retained"
        )
        assert not db._conn.execute("SELECT 1 FROM account_plans").fetchall()
