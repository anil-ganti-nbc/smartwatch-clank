from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from smartwatch_clank.core.models import CollectorTier, RunScope
from smartwatch_clank.core.qualification import (
    ExecutionProvenance,
    QualificationMaterial,
)
from smartwatch_clank.core.registry import CollectorRegistry
from smartwatch_clank.core.runner import RunProvenance, Runner
from smartwatch_clank.core.store import SQLiteStore
from tests.helpers import DummyCollector, observation


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "qualification.sqlite3"
        self.store = SQLiteStore(self.database)
        self.collector = DummyCollector(
            "qualified", tier=CollectorTier.EXPERIMENTAL,
            items=(observation("qualified", "watch-1", price="100"),),
        )
        self.registry = CollectorRegistry()
        self.registry.register(self.collector)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @staticmethod
    def provenance(revision: str | None = "rev-a",
                   trigger: ExecutionProvenance | str = ExecutionProvenance.SCHEDULED,
                   config: str | None = "config-a") -> RunProvenance:
        return RunProvenance(
            app_version="0.2.3", config_fingerprint=config, git_revision=revision, trigger=trigger
        )

    def runner(self, **kwargs) -> Runner:
        return Runner(self.registry, self.store, provenance=self.provenance(**kwargs))

    def test_authoritative_provenance_is_persisted_and_missing_is_unknown(self):
        self.runner().run(RunScope.ALL)
        row = self.store.connection.execute(
            "SELECT execution_provenance FROM runs"
        ).fetchone()
        self.assertEqual(row["execution_provenance"], "SCHEDULED")

        unknown = Runner(self.registry, self.store).run(RunScope.ALL)[0]
        self.assertTrue(unknown.healthy)
        row = self.store.connection.execute(
            "SELECT execution_provenance FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["execution_provenance"], "UNKNOWN")
        self.assertFalse(self.store.qualification_gate("qualified").eligible)
        self.assertEqual(self.store.qualification_gate("qualified").reason, "UNKNOWN_PROVENANCE")

    def test_same_material_reuses_active_epoch_without_duplicate_reset(self):
        self.runner().run(RunScope.ALL)
        first_epoch = self.store.connection.execute(
            "SELECT epoch_id FROM qualification_epochs"
        ).fetchone()["epoch_id"]
        self.runner().run(RunScope.ALL)
        rows = self.store.connection.execute(
            "SELECT event_type FROM qualification_events ORDER BY id"
        ).fetchall()
        self.assertEqual([row["event_type"] for row in rows], ["EPOCH_STARTED", "TERMINAL", "TERMINAL"])
        current_epoch = self.store.connection.execute(
            "SELECT epoch_id FROM qualification_epochs ORDER BY id DESC LIMIT 1"
        ).fetchone()["epoch_id"]
        self.assertEqual(current_epoch, first_epoch)

    def test_material_change_resets_before_first_changed_execution(self):
        self.runner().run(RunScope.ALL)
        old_run = self.store.connection.execute(
            "SELECT run_uuid,qualification_epoch_id FROM runs"
        ).fetchone()
        self.collector.items = (observation("qualified", "watch-1", price="120"),)
        changed = self.runner(revision="rev-b").run(RunScope.ALL)[0]
        self.assertTrue(changed.baseline)
        self.assertEqual(changed.discovery_count, 0)

        runs = self.store.connection.execute(
            "SELECT run_uuid,qualification_epoch_id,material_identity FROM runs ORDER BY id"
        ).fetchall()
        self.assertEqual(len(runs), 2)
        self.assertNotEqual(runs[0]["qualification_epoch_id"], runs[1]["qualification_epoch_id"])
        self.assertEqual(runs[0]["run_uuid"], old_run["run_uuid"])
        reset, terminal = self.store.connection.execute(
            "SELECT event_type,execution_id,previous_epoch_id,previous_material_identity "
            "FROM qualification_events WHERE event_type='RESET' ORDER BY id"
        ).fetchone(), self.store.connection.execute(
            "SELECT event_type,execution_id,epoch_id FROM qualification_events "
            "WHERE event_type='TERMINAL' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(reset["event_type"], "RESET")
        self.assertEqual(reset["execution_id"], terminal["execution_id"])
        self.assertEqual(reset["previous_epoch_id"], runs[0]["qualification_epoch_id"])
        self.assertEqual(reset["previous_material_identity"], runs[0]["material_identity"])

        events = self.store.qualification_events("qualified")
        self.assertEqual([item["event_type"] for item in events], ["EPOCH_STARTED", "TERMINAL", "RESET", "TERMINAL"])
        self.assertLess(
            next(i for i, item in enumerate(events) if item["event_type"] == "RESET"),
            next(i for i, item in enumerate(events) if item["event_type"] == "TERMINAL" and item["execution_id"] == terminal["execution_id"]),
        )
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 2
        )
        self.assertFalse(self.store.qualification_gate(
            "qualified", epoch_id=runs[0]["qualification_epoch_id"]
        ).eligible)
        self.assertTrue(self.store.qualification_gate(
            "qualified", epoch_id=runs[1]["qualification_epoch_id"]
        ).eligible)

    def test_reset_preparation_is_idempotent_and_terminal_is_idempotent(self):
        material_a = QualificationMaterial("0.2.3", "config-a", "rev-a", 3, "all")
        material_b = QualificationMaterial("0.2.3", "config-a", "rev-b", 3, "all")
        started = datetime(2026, 9, 1, tzinfo=timezone.utc)
        first = self.store.prepare_qualification(
            collector="qualified", execution_id="exec-a", material=material_a,
            provenance=ExecutionProvenance.SCHEDULED, started_at=started
        )
        second = self.store.prepare_qualification(
            collector="qualified", execution_id="exec-b", material=material_b,
            provenance=ExecutionProvenance.SCHEDULED, started_at=started
        )
        repeated = self.store.prepare_qualification(
            collector="qualified", execution_id="exec-b", material=material_b,
            provenance=ExecutionProvenance.SCHEDULED, started_at=started
        )
        self.assertNotEqual(first.epoch_id, second.epoch_id)
        self.assertEqual(second.epoch_id, repeated.epoch_id)
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM qualification_events WHERE event_type='RESET'"
        ).fetchone()[0], 1)
        self.store.record_qualification_terminal(
            collector="qualified", execution_id="exec-b", epoch_id=second.epoch_id,
            material=material_b, provenance=ExecutionProvenance.SCHEDULED,
            healthy=True, finished_at=started,
        )
        self.store.record_qualification_terminal(
            collector="qualified", execution_id="exec-b", epoch_id=second.epoch_id,
            material=material_b, provenance=ExecutionProvenance.SCHEDULED,
            healthy=True, finished_at=started,
        )
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM qualification_events WHERE event_type='TERMINAL'"
        ).fetchone()[0], 1)

    def test_material_identity_is_stable_and_excludes_volatile_runtime_data(self):
        first = QualificationMaterial("0.2.3", "config-a", "rev-a", 3, "production")
        same = QualificationMaterial("0.2.3", "config-a", "rev-a", 3, "production")
        changed_revision = QualificationMaterial("0.2.3", "config-a", "rev-b", 3, "production")
        changed_config = QualificationMaterial("0.2.3", "config-b", "rev-a", 3, "production")
        self.assertEqual(first.identity(), same.identity())
        self.assertNotEqual(first.identity(), changed_revision.identity())
        self.assertNotEqual(first.identity(), changed_config.identity())
        self.assertIn("git_revision", first.components())
        self.assertNotIn("timestamp", first.components())
        self.assertTrue(first.trustworthy)
        self.assertFalse(QualificationMaterial("0.2.3", "config-a", "unknown", 3, "production").trustworthy)

    def test_gate_fails_closed_without_terminal_or_with_stale_material(self):
        material = QualificationMaterial("0.2.3", "config-a", "rev-a", 3, "all")
        epoch = self.store.prepare_qualification(
            collector="qualified", execution_id="exec-a", material=material,
            provenance=ExecutionProvenance.SCHEDULED,
            started_at=datetime.now(timezone.utc),
        )
        self.assertFalse(self.store.qualification_gate("qualified").eligible)
        self.assertEqual(self.store.qualification_gate("qualified").reason, "NO_TERMINAL_EVIDENCE")
        self.store.record_qualification_terminal(
            collector="qualified", execution_id="exec-a", epoch_id=epoch.epoch_id,
            material=material, provenance=ExecutionProvenance.SCHEDULED,
            healthy=True, finished_at=datetime.now(timezone.utc),
        )
        self.assertTrue(self.store.qualification_gate(
            "qualified", material_identity=material.identity()
        ).eligible)
        self.assertFalse(self.store.qualification_gate(
            "qualified", material_identity="swq1-stale"
        ).eligible)
        self.assertEqual(self.store.qualification_gate(
            "qualified", material_identity="swq1-stale"
        ).reason, "MATERIAL_IDENTITY_MISMATCH")

    def test_gate_rejects_known_trigger_with_untrusted_material_components(self):
        material = QualificationMaterial("0.2.3", "config-a", "unknown", 3, "all")
        epoch = self.store.prepare_qualification(
            collector="qualified", execution_id="exec-unknown-material", material=material,
            provenance=ExecutionProvenance.SCHEDULED,
            started_at=datetime.now(timezone.utc),
        )
        self.store.record_qualification_terminal(
            collector="qualified", execution_id="exec-unknown-material", epoch_id=epoch.epoch_id,
            material=material, provenance=ExecutionProvenance.SCHEDULED,
            healthy=True, finished_at=datetime.now(timezone.utc),
        )
        decision = self.store.qualification_gate("qualified")
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "UNTRUSTWORTHY_MATERIAL_IDENTITY")

    def test_legacy_rows_remain_null_and_schema_migration_is_additive(self):
        self.store.close()
        legacy_database = Path(self.temp.name) / "legacy.sqlite3"
        legacy = sqlite3.connect(legacy_database)
        legacy.execute(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY, collector TEXT NOT NULL, "
            "started_at TEXT NOT NULL, finished_at TEXT NOT NULL, healthy INTEGER NOT NULL, "
            "observation_count INTEGER NOT NULL, warning TEXT, error TEXT, "
            "metadata_json TEXT NOT NULL DEFAULT '{}', discovery_count INTEGER NOT NULL DEFAULT 0)"
        )
        legacy.execute(
            "INSERT INTO runs(collector,started_at,finished_at,healthy,observation_count,warning,error) "
            "VALUES('legacy','2026-01-01','2026-01-01',1,1,NULL,NULL)"
        )
        legacy.commit()
        legacy.close()
        self.store = SQLiteStore(legacy_database)
        row = self.store.connection.execute(
            "SELECT execution_provenance,qualification_epoch_id,material_identity FROM runs"
        ).fetchone()
        self.assertIsNone(row["execution_provenance"])
        self.assertIsNone(row["qualification_epoch_id"])
        self.assertIsNone(row["material_identity"])
        self.assertEqual(self.store.schema_version(), 3)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
