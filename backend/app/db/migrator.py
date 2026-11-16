"""Versioned SQL migrations: ``migrations/NNNN_name.sql`` applied in order, once each,
each inside a transaction. Applied files are checksummed so edits to history are detected."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def discover(directory: Path = MIGRATIONS_DIR) -> List[Migration]:
    found: List[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILE.match(path.name)
        if not match:
            raise MigrationError(f"Migration file name must look like 0001_name.sql: {path.name}")
        text = path.read_text(encoding="utf-8")
        found.append(Migration(int(match.group(1)), match.group(2), text, hashlib.sha256(text.encode()).hexdigest()))
    versions = [m.version for m in found]
    if versions != sorted(set(versions)) or (versions and versions != list(range(1, len(versions) + 1))):
        raise MigrationError(f"Migration versions must be unique and consecutive from 1: {versions}")
    return found


def _split(sql: str) -> List[str]:
    statements, buffer = [], ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise MigrationError("Migration ends with an incomplete statement.")
    return statements


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )


def applied(conn: sqlite3.Connection) -> Dict[int, str]:
    _ensure_table(conn)
    return {row[0]: row[1] for row in conn.execute("SELECT version, checksum FROM schema_migrations")}  # works with any row factory


def latest_version(directory: Path = MIGRATIONS_DIR) -> int:
    found = discover(directory)
    return found[-1].version if found else 0


def pending_versions(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> List[int]:
    done = applied(conn)
    return [m.version for m in discover(directory) if m.version not in done]


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> List[int]:
    """Apply every pending migration. Returns the versions applied by this call."""
    done = applied(conn)
    newly: List[int] = []
    for migration in discover(directory):
        if migration.version in done:
            if done[migration.version] != migration.checksum:
                raise MigrationError(f"Migration {migration.version:04d}_{migration.name} was changed after it was applied.")
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split(migration.sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, datetime.now(timezone.utc).isoformat()),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        newly.append(migration.version)
    return newly
