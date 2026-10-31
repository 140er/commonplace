"""Per-workspace rules and transactional, append-only version snapshots."""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sequence(conn):
    return conn.execute("SELECT value FROM meta WHERE key='seq'").fetchone()[0]


class RuleStore:
    _initialized = set()
    _init_lock = threading.Lock()

    def __init__(self, workspace_dir):
        directory = Path(workspace_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = str((directory / "rules.sqlite3").resolve())
        with self._init_lock:
            if self.path not in self._initialized or not Path(self.path).exists():
                with self.connection() as conn:
                    conn.executescript(Path(__file__).with_name("schema.sql").read_text())
                self._initialized.add(self.path)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def read(self):
        with self.connection() as conn:
            conn.execute("BEGIN")
            try:
                yield conn
            finally:
                conn.rollback()

    @contextmanager
    def transaction(self):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @property
    def seq(self):
        with self.connection() as conn:
            return sequence(conn)
