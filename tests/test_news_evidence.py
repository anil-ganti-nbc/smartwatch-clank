import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartwatch_clank.core.models import SourceClass
from smartwatch_clank.core.store import SQLiteStore
from smartwatch_clank.intelligence.news import persist_news_evidence
from tests.helpers import observation


class NewsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _observation(self, identity: str, state: str, observed_at: datetime, oem: str = "samsung"):
        return observation(
            f"{oem}_official_news", identity, oem=oem, source_class=SourceClass.OFFICIAL_NEWS.value,
            classification_state=state, observed_at=observed_at,
        )

    def test_relevant_and_possibly_are_promoted_to_evidence_not_relevant_is_skipped(self):
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        observations = (
            self._observation("samsung:news:1", "SMARTWATCH_RELEVANT", now),
            self._observation("samsung:news:2", "POSSIBLY_SMARTWATCH_RELEVANT", now),
            self._observation("samsung:news:3", "NOT_SMARTWATCH_RELEVANT", now),
        )
        summary = persist_news_evidence(self.store, observations)
        self.assertEqual(summary["evidence_written"], 2)
        self.assertIsNotNone(self.store.get_evidence(source_class="official_news", identity="samsung:news:1"))
        self.assertIsNotNone(self.store.get_evidence(source_class="official_news", identity="samsung:news:2"))
        self.assertIsNone(self.store.get_evidence(source_class="official_news", identity="samsung:news:3"))

    def test_classification_counts_include_all_states(self):
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        observations = (
            self._observation("samsung:news:1", "SMARTWATCH_RELEVANT", now),
            self._observation("samsung:news:2", "NOT_SMARTWATCH_RELEVANT", now),
        )
        summary = persist_news_evidence(self.store, observations)
        self.assertEqual(summary["classification_counts"]["SMARTWATCH_RELEVANT"], 1)
        self.assertEqual(summary["classification_counts"]["NOT_SMARTWATCH_RELEVANT"], 1)

    def test_first_seen_is_preserved_across_repeated_persistence_calls(self):
        first_seen = datetime(2026, 8, 1, tzinfo=timezone.utc)
        later = first_seen + timedelta(days=3)
        persist_news_evidence(self.store, (self._observation("samsung:news:1", "SMARTWATCH_RELEVANT", first_seen),))
        persist_news_evidence(self.store, (self._observation("samsung:news:1", "SMARTWATCH_RELEVANT", later),))
        record = self.store.get_evidence(source_class="official_news", identity="samsung:news:1")
        self.assertEqual(record["first_seen"], first_seen.isoformat())
        self.assertEqual(record["last_seen"], later.isoformat())


if __name__ == "__main__":
    unittest.main()
