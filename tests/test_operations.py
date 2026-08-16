from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from smartwatch_clank.cli import main
from smartwatch_clank.configuration import load_runtime_config
from smartwatch_clank.core.models import CollectorTier
from smartwatch_clank.core.registry import CollectorRegistry
from smartwatch_clank.core.runner import Runner
from smartwatch_clank.core.store import SQLiteStore
from smartwatch_clank.intelligence.samsung import persist_samsung_reconciliation, reconcile_samsung
from smartwatch_clank.operations import candidates_report, health_report, recent_discoveries, scope_report, soak_summary
from tests.helpers import DummyCollector, observation
from tests.test_samsung_reconciliation import product, support


class OperationalTests(unittest.TestCase):
    def test_config_provenance_and_canonical_database_are_visible(self):
        config = load_runtime_config()
        provenance = config.provenance()
        self.assertTrue(provenance["repository_defaults"].endswith("config.yaml"))
        self.assertEqual(
            Path(provenance["database"]).parts[-2:],
            ("var", "smartwatch-clank.sqlite3"),
        )
        self.assertIn("production_allowlist", provenance)

    def test_collector_level_scope_enables_only_allowlisted_production_collectors(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("enabled", tier=CollectorTier.PRODUCTION))
        registry.register(DummyCollector("disabled", tier=CollectorTier.PRODUCTION))
        config = load_runtime_config()
        scoped = type(config)(config.runner, ("enabled",), config.database, config.sources)
        report = scope_report(registry, scoped)
        states = {item["collector"]: item["production_enabled"] for item in report["collectors"]}
        self.assertEqual(states, {"disabled": False, "enabled": True})

    def test_health_never_run_then_healthy_and_inspection_commands(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("dummy", items=(observation("dummy", "watch-1"),), tier=CollectorTier.PRODUCTION))
        config = load_runtime_config()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "health.sqlite3"
            with SQLiteStore(database) as store:
                self.assertEqual(health_report(store, registry, config)["collectors"][0]["status"], "NEVER_RUN")
                Runner(registry, store).run(CollectorTier.PRODUCTION, ("dummy",))
                self.assertEqual(health_report(store, registry, config)["collectors"][0]["status"], "HEALTHY")
                self.assertEqual(soak_summary(store)["total_runs"], 1)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--database", str(database), "health"], registry), 0)
            self.assertEqual(json.loads(output.getvalue())["collectors"][0]["status"], "HEALTHY")

    def test_candidate_inspection_shows_baseline_evidence(self):
        with tempfile.TemporaryDirectory() as directory, SQLiteStore(Path(directory) / "c.sqlite3") as store:
            records = reconcile_samsung((), (support("IN", "SM-L305", "SM-L305FZEAINS"),))
            persist_samsung_reconciliation(store, records, reconciled_at=datetime(2026, 8, 9, tzinfo=timezone.utc))
            result = candidates_report(store)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["candidates"][0]["base_model"], "SM-L305")
            self.assertTrue(result["candidates"][0]["baseline_suppressed"])

    def test_production_rejects_experimental_named_database(self):
        registry = CollectorRegistry()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(stderr), self.assertRaises(SystemExit):
            main(["--database", str(Path(directory) / "experimental.sqlite3"), "run", "--mode", "production"], registry)
        self.assertIn("refuses an experimental-named database", stderr.getvalue())

    def test_recent_discovery_inspection_preserves_price_evidence_without_discount_inference(self):
        registry = CollectorRegistry()
        collector = DummyCollector("dummy", items=(observation("dummy", "watch-1", price="100", currency="INR"),))
        registry.register(collector)
        with tempfile.TemporaryDirectory() as directory, SQLiteStore(Path(directory) / "d.sqlite3") as store:
            runner = Runner(registry, store)
            runner.run(CollectorTier.EXPERIMENTAL)
            collector.items = (observation("dummy", "watch-1", price="120", currency="INR"),)
            runner.run(CollectorTier.EXPERIMENTAL)
            result = recent_discoveries(store)
            self.assertEqual(result["discoveries"][0]["type"], "PRICE_CHANGE")
            self.assertNotIn("discount", json.dumps(result).lower())

    def test_host_migration_is_in_run_metadata_and_does_not_rebaseline(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector(
            "samsung_product_catalogue",
            items=(observation("samsung_product_catalogue", "watch-1"),), tier=CollectorTier.PRODUCTION
        ))
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "portable.sqlite3"
            with patch.dict("os.environ", {"SMARTWATCH_CLANK_HOST_ID": "host-a"}):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["--database", str(database), "run", "--mode", "production"], registry), 0)
            with patch.dict("os.environ", {"SMARTWATCH_CLANK_HOST_ID": "host-b"}):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["--database", str(database), "run", "--mode", "production"], registry), 0)
            resumed = json.loads(output.getvalue())
            self.assertFalse(resumed["outcomes"][0]["baseline"])
            self.assertEqual(resumed["cycle"]["host_migration"]["from_host_id"], "host-a")
            with SQLiteStore(database) as store:
                metadata = json.loads(store.connection.execute(
                    "SELECT metadata_json FROM runs ORDER BY id DESC LIMIT 1"
                ).fetchone()[0])
                self.assertEqual(metadata["soak"]["host_id"], "host-b")
                report = soak_summary(store)
                self.assertEqual(report["active_host_id"], "host-b")
                self.assertEqual(len(report["host_migrations"]), 1)

    def test_consistent_backup_retains_history_and_soak_state(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            backup = Path(directory) / "transfer" / "soak.sqlite3"
            with SQLiteStore(source) as store:
                store.set_soak_state("active_host_id", "host-a")
                store.backup_to(backup)
            with SQLiteStore(backup) as restored:
                self.assertEqual(restored.get_soak_state("active_host_id"), "host-a")


if __name__ == "__main__":
    unittest.main()
