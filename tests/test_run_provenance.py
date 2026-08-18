import json
import tempfile
import unittest
from pathlib import Path

from smartwatch_clank.core.models import CollectorTier
from smartwatch_clank.core.registry import CollectorRegistry
from smartwatch_clank.core.runner import Runner, RunProvenance
from smartwatch_clank.core.store import SQLiteStore
from tests.helpers import DummyCollector, observation


class RunProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_run_persists_provenance_columns(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector(items=(observation("dummy", "watch-1"),)))
        provenance = RunProvenance(app_version="9.9.9", config_fingerprint="abc123", git_revision="deadbeef")
        Runner(registry, self.store, provenance=provenance).run(CollectorTier.EXPERIMENTAL)
        row = self.store.connection.execute(
            "SELECT run_uuid, app_version, schema_version_at_run, config_fingerprint, git_revision FROM runs"
        ).fetchone()
        self.assertIsNotNone(row["run_uuid"])
        self.assertEqual(row["app_version"], "9.9.9")
        self.assertEqual(int(row["schema_version_at_run"]), SQLiteStore.SCHEMA_VERSION)
        self.assertEqual(row["config_fingerprint"], "abc123")
        self.assertEqual(row["git_revision"], "deadbeef")

    def test_each_run_gets_a_distinct_run_uuid(self):
        registry = CollectorRegistry()
        collector = DummyCollector(items=(observation("dummy", "watch-1"),))
        registry.register(collector)
        runner = Runner(registry, self.store)
        runner.run(CollectorTier.EXPERIMENTAL)
        runner.run(CollectorTier.EXPERIMENTAL)
        rows = self.store.connection.execute("SELECT run_uuid FROM runs ORDER BY id").fetchall()
        self.assertNotEqual(rows[0]["run_uuid"], rows[1]["run_uuid"])

    def test_provenance_defaults_to_none_when_unspecified(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector(items=(observation("dummy", "watch-1"),)))
        Runner(registry, self.store).run(CollectorTier.EXPERIMENTAL)
        row = self.store.connection.execute("SELECT app_version, config_fingerprint, git_revision FROM runs").fetchone()
        self.assertIsNone(row["app_version"])
        self.assertIsNone(row["config_fingerprint"])
        self.assertIsNone(row["git_revision"])

    def test_schema_version_table_reports_current_version(self):
        self.assertEqual(self.store.schema_version(), SQLiteStore.SCHEMA_VERSION)

    def test_config_fingerprint_changes_with_content_stable_otherwise(self):
        from smartwatch_clank.configuration import _fingerprint

        first = _fingerprint({"a": 1}, {"b": 2})
        again = _fingerprint({"a": 1}, {"b": 2})
        changed = _fingerprint({"a": 2}, {"b": 2})
        self.assertEqual(first, again)
        self.assertNotEqual(first, changed)


if __name__ == "__main__":
    unittest.main()
