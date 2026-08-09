import tempfile
import unittest
from pathlib import Path

from smartwatch_clank.core.models import CollectorTier
from smartwatch_clank.core.registry import CollectorRegistry
from smartwatch_clank.core.runner import Runner
from smartwatch_clank.core.store import SQLiteStore
from tests.helpers import DummyCollector, observation


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_success_baselines_then_persists_a_deterministic_change(self):
        registry = CollectorRegistry()
        collector = DummyCollector(items=(observation("dummy", "watch-1", price="100"),))
        registry.register(collector)
        runner = Runner(registry, self.store)
        first = runner.run(CollectorTier.EXPERIMENTAL)[0]
        self.assertTrue(first.healthy)
        self.assertTrue(first.baseline)
        self.assertEqual(first.discovery_count, 0)

        collector.items = (observation("dummy", "watch-1", price="120"),)
        second = runner.run(CollectorTier.EXPERIMENTAL)[0]
        self.assertFalse(second.baseline)
        self.assertEqual(second.discovery_count, 1)
        self.assertEqual(self.store.counts(), {"runs": 2, "observations": 2, "discoveries": 1})
        change = self.store.connection.execute("SELECT change_type FROM discoveries").fetchone()[0]
        self.assertEqual(change, "PRICE_CHANGE")

    def test_collector_metadata_is_persisted_for_audit(self):
        registry = CollectorRegistry()
        collector = DummyCollector(items=(observation("dummy", "watch-1"),))
        registry.register(collector)
        Runner(registry, self.store).run(CollectorTier.EXPERIMENTAL)
        metadata = self.store.connection.execute("SELECT metadata_json FROM runs").fetchone()[0]
        self.assertEqual(metadata, "{}")

    def test_exception_is_isolated_from_other_collectors(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("broken", error=RuntimeError("parser exploded")))
        registry.register(DummyCollector("healthy", (observation("healthy", "watch-1"),)))
        outcomes = Runner(registry, self.store).run(CollectorTier.EXPERIMENTAL)
        self.assertEqual([(o.collector, o.healthy) for o in outcomes], [("broken", False), ("healthy", True)])
        self.assertEqual(self.store.counts()["runs"], 2)

    def test_unexpected_zero_does_not_replace_last_healthy_catalogue(self):
        registry = CollectorRegistry()
        collector = DummyCollector(items=(observation("dummy", "watch-1"), observation("dummy", "watch-2")))
        registry.register(collector)
        runner = Runner(registry, self.store)
        runner.run(CollectorTier.EXPERIMENTAL)
        collector.items = ()
        failed = runner.run(CollectorTier.EXPERIMENTAL)[0]
        self.assertFalse(failed.healthy)
        self.assertIn("zero observations", failed.error)
        self.assertEqual(set(self.store.last_healthy_catalogue("dummy")), {"watch-1", "watch-2"})
        self.assertEqual(self.store.counts()["discoveries"], 0)

    def test_catalogue_collapse_is_failure_without_removal_discoveries(self):
        registry = CollectorRegistry()
        initial = tuple(observation("dummy", f"watch-{number}") for number in range(10))
        collector = DummyCollector(items=initial)
        registry.register(collector)
        runner = Runner(registry, self.store)
        runner.run(CollectorTier.EXPERIMENTAL)
        collector.items = initial[:4]
        outcome = runner.run(CollectorTier.EXPERIMENTAL)[0]
        self.assertFalse(outcome.healthy)
        self.assertIn("catalogue collapse", outcome.error)
        self.assertEqual(self.store.counts()["discoveries"], 0)

    def test_empty_production_allowlist_is_a_clean_noop(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("ready", tier=CollectorTier.PRODUCTION,
                                         items=(observation("ready", "watch-1"),)))
        outcomes = Runner(registry, self.store).run(CollectorTier.PRODUCTION, ())
        self.assertEqual(outcomes, [])
        self.assertEqual(self.store.counts()["runs"], 0)

    def test_disabled_collector_is_not_attempted_while_enabled_collector_runs(self):
        registry = CollectorRegistry()
        enabled = DummyCollector("enabled", tier=CollectorTier.PRODUCTION,
                                 items=(observation("enabled", "watch-1"),))
        disabled = DummyCollector("disabled", tier=CollectorTier.PRODUCTION,
                                  error=AssertionError("disabled collector was called"))
        registry.register(enabled)
        registry.register(disabled)
        outcomes = Runner(registry, self.store).run(CollectorTier.PRODUCTION, ("enabled",))
        self.assertEqual([(item.collector, item.healthy) for item in outcomes], [("enabled", True)])

    def test_healthy_recovery_compares_against_last_healthy_not_rejected_snapshot(self):
        registry = CollectorRegistry()
        initial = tuple(observation("dummy", f"watch-{number}") for number in range(10))
        collector = DummyCollector(items=initial)
        registry.register(collector)
        runner = Runner(registry, self.store)
        runner.run(CollectorTier.EXPERIMENTAL)
        collector.items = initial[:2]
        self.assertFalse(runner.run(CollectorTier.EXPERIMENTAL)[0].healthy)
        collector.items = initial + (observation("dummy", "watch-new"),)
        recovered = runner.run(CollectorTier.EXPERIMENTAL)[0]
        self.assertTrue(recovered.healthy)
        self.assertEqual(recovered.discovery_count, 1)
        self.assertEqual(set(self.store.last_healthy_catalogue("dummy")), {*(f"watch-{n}" for n in range(10)), "watch-new"})


if __name__ == "__main__":
    unittest.main()
