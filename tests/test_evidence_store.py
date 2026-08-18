import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartwatch_clank.core.store import SQLiteStore


class EvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_first_write_sets_first_seen_and_last_seen(self):
        observed_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        evidence_id = self.store.record_evidence(
            oem="samsung", source_class="certification", identity="SM-L999",
            observed_at=observed_at, region="global", confidence="HIGH", source_url="https://fcc.example/x",
        )
        record = self.store.get_evidence(source_class="certification", identity="SM-L999")
        self.assertEqual(record["id"], evidence_id)
        self.assertEqual(record["first_seen"], observed_at.isoformat())
        self.assertEqual(record["last_seen"], observed_at.isoformat())

    def test_later_evidence_never_overwrites_earlier_first_seen(self):
        first_seen = datetime(2026, 8, 1, tzinfo=timezone.utc)
        later = first_seen + timedelta(days=4)
        self.store.record_evidence(
            oem="samsung", source_class="certification", identity="SM-L999", observed_at=first_seen,
        )
        self.store.record_evidence(
            oem="samsung", source_class="certification", identity="SM-L999", observed_at=later,
            confidence="MEDIUM",
        )
        record = self.store.get_evidence(source_class="certification", identity="SM-L999")
        self.assertEqual(record["first_seen"], first_seen.isoformat())
        self.assertEqual(record["last_seen"], later.isoformat())
        self.assertEqual(record["confidence"], "MEDIUM")

    def test_same_identity_keeps_independent_first_seen_per_source_class(self):
        certification_seen = datetime(2026, 8, 1, tzinfo=timezone.utc)
        support_seen = datetime(2026, 8, 5, tzinfo=timezone.utc)
        self.store.record_evidence(
            oem="samsung", source_class="certification", identity="SM-L999", observed_at=certification_seen,
        )
        self.store.record_evidence(
            oem="samsung", source_class="support", identity="SM-L999", observed_at=support_seen,
        )
        certification = self.store.get_evidence(source_class="certification", identity="SM-L999")
        support = self.store.get_evidence(source_class="support", identity="SM-L999")
        self.assertEqual(certification["first_seen"], certification_seen.isoformat())
        self.assertEqual(support["first_seen"], support_seen.isoformat())

    def test_evidence_timeline_preserves_ordered_events(self):
        observed_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        evidence_id = self.store.record_evidence(
            oem="samsung", source_class="certification", identity="SM-L999", observed_at=observed_at,
        )
        self.store.record_evidence_event(evidence_id=evidence_id, observed_at=observed_at, event="FIRST_SEEN")
        self.store.record_evidence_event(
            evidence_id=evidence_id, observed_at=observed_at + timedelta(days=1), event="CONFIRMED",
            payload={"source": "support"},
        )
        timeline = self.store.evidence_timeline(evidence_id)
        self.assertEqual([row["event"] for row in timeline], ["FIRST_SEEN", "CONFIRMED"])

    def test_unresolved_identity_is_a_valid_evidence_record(self):
        evidence_id = self.store.record_evidence(
            oem="samsung", source_class="certification", identity="SM-L999",
            observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc), payload={"marketing_identity": None},
        )
        record = self.store.get_evidence(source_class="certification", identity="SM-L999")
        self.assertIsNotNone(record)
        self.assertEqual(record["id"], evidence_id)


if __name__ == "__main__":
    unittest.main()
