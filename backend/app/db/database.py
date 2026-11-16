"""SQLite access. Connections are short-lived (one per request) and run in autocommit
mode; multi-statement changes use :func:`transaction` explicitly."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._ready = False
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def ensure_ready(self, *, auto_migrate: bool) -> None:
        """Make sure the schema is current before serving requests (once per process)."""
        from .migrator import MigrationError, migrate, pending_versions

        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self.session() as conn:
                if auto_migrate:
                    migrate(conn)
                elif pending_versions(conn):
                    raise MigrationError("The database schema is not up to date. Run: python -m app.cli migrate")
            self._ready = True


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Atomic block (BEGIN IMMEDIATE). Joins an already-open transaction instead of nesting."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
