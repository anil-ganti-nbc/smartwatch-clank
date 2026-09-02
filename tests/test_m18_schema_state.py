"""M18 persistent-state compatibility barrier for Smartwatch (STD-DEPLOY-COM-002).

Family ADDITIVE_SCHEMA_MARKER_COMPATIBILITY: the store always had a durable
monotonic `schema_version` marker and additive-only migrations, but no
compatibility decision — every read-write open layered the current schema
over whatever it found. M18 adds the decision: read-only inspection first,
canonical additive bootstrap/migration only for genuinely fresh or
recognized-older state, refusal with evidence for everything else.

Every test uses disposable SQLite files. No live collectors run.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from smartwatch_clank.cli import main
from smartwatch_clank.core.schema_state import (
    EXPECTED_SCHEMA_VERSION,
    UNADMITTABLE_STATES,
    SchemaState,
    SchemaStateError,
    inspect_schema,
    inspect_store,
)
from smartwatch_clank.core.store import SQLiteStore

V1_RUNS_COLUMNS = [
    "id", "collector", "started_at", "finished_at", "healthy",
    "observation_count", "warning", "error",
]
V2_RUNS_COLUMNS = V1_RUNS_COLUMNS + ["metadata_json", "discovery_count"]

# States a read-only command surface refuses (it cannot migrate): the
# store-level gate admits MIGRATION_REQUIRED through canonical migration,
# but a read-only inspection command must not serve pre-migration state.
CLI_REFUSAL_STATES = UNADMITTABLE_STATES | {SchemaState.MIGRATION_REQUIRED}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _con(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(path))


def _legacy_database(tmp: Path, name: str, columns: list[str]) -> Path:
    """An honest pre-marker Smartwatch database: exactly the historical
    `runs` table at one generation's column set, with a legacy row."""
    db = tmp / name
    con = _con(db)
    con.execute(f"CREATE TABLE runs ({', '.join(columns)})")
    if "metadata_json" in columns:
        con.execute(
            "INSERT INTO runs(collector,started_at,finished_at,healthy,"
            "observation_count,warning,error,metadata_json,discovery_count) "
            "VALUES('legacy','2026-01-01','2026-01-01',1,1,NULL,NULL,'{}',1)"
        )
    else:
        con.execute(
            "INSERT INTO runs(collector,started_at,finished_at,healthy,"
            "observation_count,warning,error) "
            "VALUES('legacy','2026-01-01','2026-01-01',1,1,NULL,NULL)"
        )
    con.commit()
    con.close()
    return db


def _seed_without_marker(tmp: Path, name: str) -> Path:
    """A v3-shaped database with its authority removed: recognized-unknown
    state that every command surface must refuse."""
    seed = tmp / "seed.db"
    SQLiteStore(seed).close()
    db = tmp / name
    shutil.copy(seed, db)
    con = _con(db)
    con.execute("DROP TABLE schema_version")
    con.commit()
    con.close()
    return db


class FreshAndCompatibleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_1_truly_fresh_db_classified_fresh(self):
        empty = self.dir / "empty.db"
        empty.write_bytes(b"")
        con = _con(empty)
        try:
            report = inspect_schema(con)
        finally:
            con.close()
        self.assertIs(report.state, SchemaState.FRESH)

    def test_2_fresh_bootstrap_succeeds_canonically(self):
        db = self.dir / "fresh.db"
        store = SQLiteStore(db)
        try:
            self.assertEqual(store.schema_version(), EXPECTED_SCHEMA_VERSION)
            tables = {
                r[0] for r in store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
            self.assertIn("qualification_events", tables)  # OPS-COM-003 shares the store
        finally:
            store.close()

    def test_3_fresh_bootstrap_reverified(self):
        db = self.dir / "fresh.db"
        SQLiteStore(db).close()
        report = inspect_store(db)
        self.assertIs(report.state, SchemaState.COMPATIBLE)
        self.assertEqual(report.observed_version, EXPECTED_SCHEMA_VERSION)

    def test_4_expected_v3_state_is_compatible(self):
        db = self.dir / "current.db"
        SQLiteStore(db).close()
        store = SQLiteStore(db)
        try:
            report = inspect_store(db)
            self.assertIs(report.state, SchemaState.COMPATIBLE)
            self.assertEqual(report.observed_version, 3)
        finally:
            store.close()

    def test_5_compatible_inspection_non_mutating(self):
        db = self.dir / "stable.db"
        SQLiteStore(db).close()
        before = _sha(db)
        store = SQLiteStore(db)
        store.close()
        self.assertEqual(_sha(db), before)


class OlderStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_7_recognized_legacy_generation_migrates_canonically(self):
        db = _legacy_database(self.dir, "v2legacy.db", V2_RUNS_COLUMNS)
        report = inspect_store(db)
        self.assertIs(report.state, SchemaState.MIGRATION_REQUIRED)
        self.assertEqual(report.observed_version, 2)
        store = SQLiteStore(db)
        try:
            row = store.connection.execute(
                "SELECT execution_provenance, qualification_epoch_id, material_identity "
                "FROM runs WHERE collector='legacy'"
            ).fetchone()
            self.assertIsNone(row["execution_provenance"])
            self.assertIsNone(row["qualification_epoch_id"])
            self.assertIsNone(row["material_identity"])
            self.assertEqual(store.schema_version(), 3)
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1
            )
        finally:
            store.close()

    def test_7b_v1_generation_recognized_and_migrates(self):
        db = _legacy_database(self.dir, "v1legacy.db", V1_RUNS_COLUMNS)
        report = inspect_store(db)
        self.assertIs(report.state, SchemaState.MIGRATION_REQUIRED)
        self.assertEqual(report.observed_version, 1)
        store = SQLiteStore(db)
        try:
            self.assertEqual(store.schema_version(), 3)
        finally:
            store.close()

    def test_8_migrated_state_explicitly_reverified(self):
        db = _legacy_database(self.dir, "v2legacy.db", V2_RUNS_COLUMNS)
        store = SQLiteStore(db)
        try:
            report = inspect_store(db)
            self.assertIs(report.state, SchemaState.COMPATIBLE)
            self.assertEqual(report.observed_version, EXPECTED_SCHEMA_VERSION)
        finally:
            store.close()

    def test_15_sabotaged_migration_cannot_mark_ready(self):
        """A failed canonical migration leaves the state unadmitted with the
        failure preserved as evidence — never half-stamped, never ready."""
        db = _legacy_database(self.dir, "v2legacy.db", V2_RUNS_COLUMNS)
        original = SQLiteStore._migrate

        def sabotaged(store_self):
            raise sqlite3.OperationalError("sabotaged migration")

        SQLiteStore._migrate = sabotaged
        try:
            with self.assertRaises(SchemaStateError) as ctx:
                SQLiteStore(db)
            self.assertIn(
                "sabotaged migration",
                ctx.exception.report.evidence.get("admission_failure", ""),
            )
        finally:
            SQLiteStore._migrate = original
        report = inspect_store(db)
        self.assertIs(report.state, SchemaState.MIGRATION_REQUIRED)
        store = SQLiteStore(db)
        try:
            self.assertEqual(store.schema_version(), 3)
        finally:
            store.close()


class RefusalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_9_newer_v4_state_fails_closed(self):
        db = self.dir / "newer.db"
        SQLiteStore(db).close()
        con = _con(db)
        con.execute("UPDATE schema_version SET version = 4")
        con.commit()
        con.close()
        before = _sha(db)
        with self.assertRaises(SchemaStateError) as ctx:
            SQLiteStore(db)
        report = ctx.exception.report
        self.assertIs(report.state, SchemaState.INCOMPATIBLE_NEWER)
        self.assertEqual(report.observed_version, 4)
        self.assertIn("FORWARD_ONLY_EXPLICIT", report.reason)
        json.dumps(report.as_evidence())  # JSON-serializable evidence
        self.assertEqual(_sha(db), before)  # byte-identical refusal
        con = _con(db)
        self.assertEqual(
            con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], 4
        )
        con.close()

    def test_10_missing_marker_fails_closed(self):
        db = self.dir / "nomarker.db"
        SQLiteStore(db).close()
        con = _con(db)
        con.execute("DROP TABLE schema_version")
        con.commit()
        con.close()
        report = inspect_store(db)
        self.assertIs(report.state, SchemaState.UNKNOWN)
        before = _sha(db)
        with self.assertRaises(SchemaStateError):
            SQLiteStore(db)
        self.assertEqual(_sha(db), before)

    def test_11_malformed_marker_fails_closed(self):
        db = self.dir / "malformed.db"
        SQLiteStore(db).close()
        con = _con(db)
        con.execute("DROP TABLE schema_version")
        con.execute("CREATE TABLE schema_version (garbage TEXT)")
        con.execute("INSERT INTO schema_version VALUES ('oops')")
        con.commit()
        con.close()
        report = inspect_store(db)
        self.assertIs(report.state, SchemaState.UNKNOWN)
        with self.assertRaises(SchemaStateError):
            SQLiteStore(db)

    def test_12_marker_schema_contradiction_fails_closed(self):
        db = self.dir / "contradiction.db"
        SQLiteStore(db).close()
        con = _con(db)
        con.execute("DROP TABLE soak_state")
        con.commit()
        con.close()
        report = inspect_store(db)
        self.assertIs(report.state, SchemaState.PARTIAL)
        self.assertIn("soak_state", report.evidence["missing_tables"])
        with self.assertRaises(SchemaStateError):
            SQLiteStore(db)

    def test_13_partial_and_unknown_states_fail_closed(self):
        # missing required column at v3: PARTIAL
        db = self.dir / "missingcol.db"
        SQLiteStore(db).close()
        con = _con(db)
        con.execute("ALTER TABLE runs DROP COLUMN material_identity")
        con.commit()
        con.close()
        report = inspect_store(db)
        self.assertIs(report.state, SchemaState.PARTIAL)
        self.assertIn("runs", report.evidence["tables_missing_columns"])
        with self.assertRaises(SchemaStateError):
            SQLiteStore(db)
        # foreign tables only: UNKNOWN
        db2 = self.dir / "foreign.db"
        con = _con(db2)
        con.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        con.commit()
        con.close()
        self.assertIs(inspect_store(db2).state, SchemaState.UNKNOWN)

    def test_14_corrupt_db_fails_closed(self):
        db = self.dir / "junk.db"
        db.write_bytes(b"not a database" * 64)
        report = inspect_store(db)
        self.assertIs(report.state, SchemaState.CORRUPT)
        with self.assertRaises(SchemaStateError):
            SQLiteStore(db)

    def test_22_older_software_newer_state_rejected(self):
        self.assertEqual(EXPECTED_SCHEMA_VERSION, 3)
        self.assertIn(SchemaState.INCOMPATIBLE_NEWER, UNADMITTABLE_STATES)
        # the marker never decreases (monotonic authority), and newer state
        # is refused rather than silently tolerated
        db = self.dir / "newer.db"
        SQLiteStore(db).close()
        con = _con(db)
        con.execute("UPDATE schema_version SET version = 5")
        con.commit()
        con.close()
        with self.assertRaises(SchemaStateError) as ctx:
            SQLiteStore(db)
        self.assertEqual(ctx.exception.report.observed_version, 5)


class InspectionPurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_23b_inspection_never_mutates_any_state(self):
        legacy = _legacy_database(self.dir, "legacy.db", V2_RUNS_COLUMNS)
        before = _sha(legacy)
        for _ in range(3):
            self.assertIs(
                inspect_store(legacy).state, SchemaState.MIGRATION_REQUIRED
            )
        self.assertEqual(_sha(legacy), before)

        current = self.dir / "current.db"
        SQLiteStore(current).close()
        before = _sha(current)
        for _ in range(3):
            self.assertIs(inspect_store(current).state, SchemaState.COMPATIBLE)
        self.assertEqual(_sha(current), before)


class QualificationAndOrthogonalityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_19_20_direct_store_and_qualification_cannot_bypass(self):
        """Qualification lives inside an admitted store: an unadmittable
        database can never be constructed into one, so neither a qualified
        collector nor any provenance/epoch state can touch it."""
        db = self.dir / "nomarker.db"
        SQLiteStore(db).close()
        con = _con(db)
        con.execute("DROP TABLE schema_version")
        con.commit()
        con.close()
        before = _sha(db)
        with self.assertRaises(SchemaStateError):
            SQLiteStore(db)
        self.assertEqual(_sha(db), before)

    def test_20b_qualification_flow_works_on_admitted_store(self):
        """OPS-COM-003 semantics are untouched on a compatible store: the
        SCHEDULED provenance still earns a qualifying terminal event."""
        from smartwatch_clank.core.qualification import (
            ExecutionProvenance,
            QualificationMaterial,
        )

        db = self.dir / "qualified.db"
        store = SQLiteStore(db)
        try:
            material = QualificationMaterial("0.2.3", "cfg", "c340a45", 3, "all")
            epoch = store.prepare_qualification(
                collector="qualified", execution_id="m18-exec",
                material=material, provenance=ExecutionProvenance.SCHEDULED,
                started_at=datetime.now(),
            )
            store.record_qualification_terminal(
                collector="qualified", execution_id="m18-exec",
                epoch_id=epoch.epoch_id, material=material,
                provenance=ExecutionProvenance.SCHEDULED, healthy=True,
                finished_at=datetime.now(),
            )
            self.assertTrue(
                store.qualification_gate("qualified", epoch_id=epoch.epoch_id).eligible
            )
        finally:
            store.close()


class CliSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_16_scheduler_and_cli_run_refuse_with_evidence(self):
        db = _seed_without_marker(self.dir, "run_refusal.db")
        before = _sha(db)
        rc = main(["--database", str(db), "run", "--mode", "production",
                   "--trigger", "MANUAL"])
        self.assertEqual(rc, 3)
        self.assertEqual(_sha(db), before)

    def test_17_21_read_commands_refuse_without_repairing(self):
        db = _seed_without_marker(self.dir, "read_refusal.db")
        before = _sha(db)
        for command in ("health", "discoveries", "soak"):
            with self.subTest(command=command):
                rc = main(["--database", str(db), command])
                self.assertEqual(rc, 3)
                self.assertEqual(_sha(db), before)

    def test_21b_health_on_compatible_store_remains_read_only(self):
        db = self.dir / "current.db"
        SQLiteStore(db).close()
        before = _sha(db)
        rc = main(["--database", str(db), "health"])
        self.assertEqual(rc, 0)
        self.assertEqual(_sha(db), before)


if __name__ == "__main__":
    unittest.main()
