from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from smartwatch_clank.configuration import load_runtime_config
from smartwatch_clank.core.models import CollectorTier
from smartwatch_clank.core.registry import CollectorRegistry
from smartwatch_clank.core.store import SQLiteStore
from smartwatch_clank.local_collection import run_finalized
from tests.helpers import DummyCollector, observation


class RunFinalizedTests(unittest.TestCase):
    """Unit coverage for the function backing both the dashboard's "Run all
    finalized collectors" button and each individual per-collector run
    button. Exercises real Runner/RunLock/SQLiteStore machinery with
    DummyCollector fixtures -- no network, no dashboard HTTP layer."""

    def _config(self, database: Path, allowlist: tuple[str, ...]):
        base = load_runtime_config()
        return type(base)(base.runner, allowlist, database, base.sources)

    def test_run_finalized_defaults_to_the_full_allowlist(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("finalized_a", items=(observation("finalized_a", "w1"),), tier=CollectorTier.PRODUCTION))
        registry.register(DummyCollector("finalized_b", items=(observation("finalized_b", "w2"),), tier=CollectorTier.PRODUCTION))
        registry.register(DummyCollector("experimental_c", items=(observation("experimental_c", "w3"),), tier=CollectorTier.EXPERIMENTAL))
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "run-all.sqlite3"
            config = self._config(database, ("finalized_a", "finalized_b"))
            result = run_finalized(config, registry)
        names = {item["collector"] for item in result["outcomes"]}
        self.assertEqual(names, {"finalized_a", "finalized_b"})
        self.assertTrue(all(item["healthy"] for item in result["outcomes"]))

    def test_run_finalized_never_reaches_an_experimental_collector(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("finalized_a", items=(observation("finalized_a", "w1"),), tier=CollectorTier.PRODUCTION))
        registry.register(DummyCollector("soak_x", items=(observation("soak_x", "w9"),), tier=CollectorTier.EXPERIMENTAL))
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "run-all.sqlite3"
            config = self._config(database, ("finalized_a",))
            # Even if a caller tried to sneak an experimental name into the
            # explicit `names` selection, Runner.run_selected rejects it --
            # there is no path through this function that runs soak_x.
            with self.assertRaises(ValueError):
                run_finalized(config, registry, ("soak_x",))

    def test_run_finalized_single_collector(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("finalized_a", items=(observation("finalized_a", "w1"),), tier=CollectorTier.PRODUCTION))
        registry.register(DummyCollector("finalized_b", items=(observation("finalized_b", "w2"),), tier=CollectorTier.PRODUCTION))
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "run-one.sqlite3"
            config = self._config(database, ("finalized_a", "finalized_b"))
            result = run_finalized(config, registry, ("finalized_a",))
        self.assertEqual([item["collector"] for item in result["outcomes"]], ["finalized_a"])

    def test_run_finalized_first_run_is_a_silent_baseline_not_a_flood(self):
        """The exact baseline-vs-novelty property the whole fleet cares
        about, exercised directly against `run_finalized`: a first healthy
        run of a catalogue with N items must report baseline=True and
        discoveries=0, never N "new" discoveries."""
        registry = CollectorRegistry()
        items = tuple(observation("finalized_a", f"w{i}") for i in range(25))
        registry.register(DummyCollector("finalized_a", items=items, tier=CollectorTier.PRODUCTION))
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "baseline.sqlite3"
            config = self._config(database, ("finalized_a",))
            result = run_finalized(config, registry)
            with SQLiteStore(database) as store:
                discovery_count = store.connection.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0]
        outcome = result["outcomes"][0]
        self.assertTrue(outcome["baseline"])
        self.assertEqual(outcome["discoveries"], 0)
        self.assertEqual(discovery_count, 0)


if __name__ == "__main__":
    unittest.main()
