"""SQLiteStore(read_only=True) regression coverage.

Part of the deploy-script backup-path fix: `_migrate()` is unconditional
DDL/DML (ALTER TABLE, INSERT ... ON CONFLICT, commit) that needs a writable
connection even when every statement is a structural no-op against an
already-migrated database -- exactly the write a genuinely read-only-mounted
source cannot make. `read_only=True` opens via a `mode=ro` URI and skips
_migrate() entirely, since backup only ever reads the source.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartwatch_clank.core.store import SQLiteStore


class ReadOnlyStoreTests(unittest.TestCase):
    def test_read_only_never_calls_migrate(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "sw.sqlite3"
            with SQLiteStore(db) as store:
                store.get_soak_state("probe")  # opens + migrates schema

            with mock.patch.object(SQLiteStore, "_migrate") as migrate:
                with SQLiteStore(db, read_only=True) as store:
                    self.assertTrue(store.read_only)
                migrate.assert_not_called()

    def test_read_only_can_read_data_written_by_a_normal_store(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "sw.sqlite3"
            with SQLiteStore(db) as store:
                store.set_soak_state("active_host_id", "hetzner-clank-fleet-01")

            with SQLiteStore(db, read_only=True) as store:
                self.assertEqual(store.get_soak_state("active_host_id"), "hetzner-clank-fleet-01")

    def test_read_only_connection_genuinely_cannot_write(self):
        """Not just "doesn't write" by convention -- the connection itself
        must be incapable of writing, so a future code change can't
        accidentally start mutating a source this mode is supposed to
        leave untouched."""
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "sw.sqlite3"
            with SQLiteStore(db) as store:
                store.get_soak_state("probe")

            with SQLiteStore(db, read_only=True) as store:
                with self.assertRaises(sqlite3.OperationalError):
                    store.connection.execute("INSERT INTO soak_state(key,value) VALUES('x','y')")

    def test_read_only_against_a_missing_database_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "never-created.sqlite3"
            with self.assertRaises(sqlite3.OperationalError):
                SQLiteStore(db, read_only=True)

    def test_default_constructor_behavior_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "sw.sqlite3"
            with SQLiteStore(db) as store:
                self.assertFalse(store.read_only)
                store.set_soak_state("k", "v")
                self.assertEqual(store.get_soak_state("k"), "v")


if __name__ == "__main__":
    unittest.main()
