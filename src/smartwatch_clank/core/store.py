from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Discovery, HealthRecord, Observation
from .qualification import (
    ExecutionProvenance,
    QualificationEpoch,
    QualificationGate,
    QualificationMaterial,
    normalize_provenance,
    new_epoch_id,
)
from .schema_state import (
    EXPECTED_SCHEMA_VERSION,
    SchemaState,
    SchemaStateError,
    inspect_schema,
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class SQLiteStore:
    # The expected persistent-state contract, in one authoritative place
    # (re-exported from core.schema_state so the schema, the migration
    # stamp, and the compatibility gate cannot drift apart). Bumped
    # whenever a schema change is made. Additive-only (CREATE TABLE IF
    # NOT EXISTS / guarded ALTER TABLE) so historical data is never dropped;
    # see the Expansion Stage A report for what each version added.
    SCHEMA_VERSION = EXPECTED_SCHEMA_VERSION

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            # For read-only callers (currently: `backup`, which only ever
            # reads the source via Connection.backup() -- it never needs
            # the source's own schema migrated). Opened via a `mode=ro`
            # URI rather than a plain read-write connect(): _migrate()
            # below is unconditional DDL/DML (ALTER TABLE, INSERT ... ON
            # CONFLICT, commit), which needs a writable connection even
            # when every statement is a structural no-op -- exactly the
            # write a genuinely read-only-mounted source (e.g. a deploy
            # script's pre-deploy backup step) cannot make. Skipping
            # _migrate() entirely here is correct, not a shortcut: backup
            # copies whatever schema already exists in the source, and
            # every real writer already runs _migrate() on its own
            # non-read-only SQLiteStore before this ever sees the file.
            # M18: this makes read-only construction an inspection-class
            # surface -- it never mutates and never gates; refusal of
            # incompatible state happens in the read-write path and in
            # the compatibility-aware commands.
            self.connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            self.connection.row_factory = sqlite3.Row
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._admit_compatibility()

    def _admit_compatibility(self) -> None:
        """M18 / STD-DEPLOY-COM-002: construction is the barrier.

        Read-only inspection decides BEFORE anything mutates: a compatible
        store opens with zero schema writes; a genuinely fresh store
        bootstraps through the canonical additive _migrate() and is
        re-verified read-only; every other state (older marked, newer,
        marker-less, partial, corrupt) raises SchemaStateError with full
        evidence and leaves the file untouched. Ordinary construction can
        therefore never launder unknown state toward "current", never
        silently accepts a newer database, and can never be bypassed by
        qualification, provenance, or run history — those all live inside
        an admitted store. Sets self.connection on every admitted path.
        """
        from .schema_state import (
            EXPECTED_SCHEMA_VERSION,
            SchemaState,
            SchemaStateError,
            _verdict,
            inspect_store,
        )

        if not self.path.exists():
            self.connection = sqlite3.connect(self.path)
            self.connection.row_factory = sqlite3.Row
            try:
                self._migrate()
            except sqlite3.Error as exc:
                post = _verdict(
                    SchemaState.UNKNOWN, EXPECTED_SCHEMA_VERSION, None,
                    f"bootstrap failed and the store is not ready: {exc}",
                    admission_failure=f"{type(exc).__name__}: {exc}",
                )
                self.connection.close()
                raise SchemaStateError(post) from exc
            post = inspect_store(self.path)
            if post.state is not SchemaState.COMPATIBLE:
                post.evidence["admission_failure"] = (
                    "bootstrap did not produce compatible state"
                )
                self.connection.close()
                raise SchemaStateError(post)
            return

        ro = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        try:
            report = inspect_schema(ro)
        finally:
            ro.close()
        admissible_through_mutation = (
            SchemaState.COMPATIBLE, SchemaState.FRESH, SchemaState.MIGRATION_REQUIRED,
        )
        if report.state not in admissible_through_mutation:
            raise SchemaStateError(report)

        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        if report.state in (SchemaState.FRESH, SchemaState.MIGRATION_REQUIRED):
            # Genuinely fresh (zero tables) or older marked state (v1/v2):
            # both are known-safe for the canonical additive _migrate(),
            # which layers the current schema and advances the monotonic
            # marker only through its guarded steps. Re-verified read-only
            # below before the store is admitted.
            try:
                self._migrate()
            except sqlite3.Error as exc:
                post = _verdict(
                    SchemaState.UNKNOWN, EXPECTED_SCHEMA_VERSION, None,
                    f"bootstrap failed and the store is not ready: {exc}",
                    admission_failure=f"{type(exc).__name__}: {exc}",
                )
                self.connection.close()
                raise SchemaStateError(post) from exc
            post = inspect_store(self.path)
            if post.state is not SchemaState.COMPATIBLE:
                post.evidence["admission_failure"] = (
                    "bootstrap did not produce compatible state"
                )
                self.connection.close()
                raise SchemaStateError(post)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        self.connection.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, collector TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL, healthy INTEGER NOT NULL, observation_count INTEGER NOT NULL,
                warning TEXT, error TEXT, metadata_json TEXT NOT NULL DEFAULT '{}', discovery_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), collector TEXT NOT NULL,
                identity TEXT NOT NULL, observed_at TEXT NOT NULL, source_url TEXT NOT NULL, data_json TEXT NOT NULL,
                UNIQUE(run_id, identity)
            );
            CREATE TABLE IF NOT EXISTS discoveries (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), collector TEXT NOT NULL,
                identity TEXT NOT NULL, change_type TEXT NOT NULL, confidence TEXT NOT NULL,
                editorial_level TEXT NOT NULL, source_url TEXT NOT NULL, discovered_at TEXT NOT NULL,
                previous_json TEXT, current_json TEXT, evidence_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS collector_health (
                collector TEXT PRIMARY KEY, healthy INTEGER NOT NULL, observed_count INTEGER NOT NULL,
                previous_count INTEGER, warning TEXT, error TEXT, checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY, discovery_id INTEGER NOT NULL REFERENCES discoveries(id),
                outcome TEXT NOT NULL CHECK(outcome IN ('HIT','INTERESTING','NOISE','BUG')),
                reason_code TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS samsung_reconciliation_runs (
                id INTEGER PRIMARY KEY, reconciled_at TEXT NOT NULL, baseline INTEGER NOT NULL,
                source_regions_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS samsung_reconciliation_records (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES samsung_reconciliation_runs(id),
                relationship TEXT NOT NULL, region TEXT NOT NULL, base_model TEXT, regional_sku TEXT,
                data_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_onboarding (
                source_kind TEXT NOT NULL, region TEXT NOT NULL, first_baselined_at TEXT NOT NULL,
                PRIMARY KEY(source_kind,region)
            );
            CREATE TABLE IF NOT EXISTS prelaunch_candidates (
                candidate_key TEXT PRIMARY KEY, base_model TEXT, regional_sku TEXT NOT NULL, region TEXT NOT NULL,
                support_url TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                classification_evidence_json TEXT NOT NULL, state TEXT NOT NULL, matched_catalogue_json TEXT,
                catalogue_first_seen TEXT, onboarding_baseline INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS samsung_candidate_events (
                id INTEGER PRIMARY KEY, candidate_key TEXT NOT NULL REFERENCES prelaunch_candidates(candidate_key),
                event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, evidence_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS soak_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS soak_host_migrations (
                id INTEGER PRIMARY KEY, from_host_id TEXT NOT NULL, to_host_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL, observation_gap_seconds REAL
            );
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY CHECK(id = 1), version INTEGER NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_records (
                id INTEGER PRIMARY KEY, oem TEXT NOT NULL, source_class TEXT NOT NULL, identity TEXT NOT NULL,
                region TEXT, confidence TEXT, editorial_level TEXT, source_url TEXT,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(source_class, identity)
            );
            CREATE TABLE IF NOT EXISTS evidence_timeline (
                id INTEGER PRIMARY KEY, evidence_id INTEGER NOT NULL REFERENCES evidence_records(id),
                observed_at TEXT NOT NULL, event TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS qualification_epochs (
                id INTEGER PRIMARY KEY, collector TEXT NOT NULL, epoch_id TEXT NOT NULL,
                material_identity TEXT NOT NULL, material_components_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL, created_by_execution_id TEXT NOT NULL,
                provenance TEXT NOT NULL, reason TEXT NOT NULL,
                previous_epoch_id TEXT, previous_material_identity TEXT,
                UNIQUE(collector, epoch_id)
            );
            CREATE INDEX IF NOT EXISTS idx_qualification_epochs_current
                ON qualification_epochs(collector, id);
            CREATE TABLE IF NOT EXISTS qualification_events (
                id INTEGER PRIMARY KEY, collector TEXT NOT NULL, event_type TEXT NOT NULL,
                execution_id TEXT NOT NULL, epoch_id TEXT NOT NULL, material_identity TEXT NOT NULL,
                material_components_json TEXT NOT NULL DEFAULT '{}', provenance TEXT NOT NULL,
                healthy INTEGER, reason TEXT NOT NULL DEFAULT '',
                previous_epoch_id TEXT, previous_material_identity TEXT, occurred_at TEXT NOT NULL,
                UNIQUE(collector, event_type, execution_id)
            );
            CREATE INDEX IF NOT EXISTS idx_qualification_events_epoch
                ON qualification_events(collector, epoch_id, event_type, id);
        """)
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(runs)")}
        if "metadata_json" not in columns:
            self.connection.execute("ALTER TABLE runs ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        if "discovery_count" not in columns:
            self.connection.execute("ALTER TABLE runs ADD COLUMN discovery_count INTEGER NOT NULL DEFAULT 0")
        for extra_column in ("run_uuid", "app_version", "schema_version_at_run", "config_fingerprint", "git_revision",
                             "execution_provenance", "qualification_epoch_id", "material_identity"):
            if extra_column not in columns:
                self.connection.execute(f"ALTER TABLE runs ADD COLUMN {extra_column} TEXT")
        self.connection.execute(
            "INSERT INTO schema_version(id,version,updated_at) VALUES(1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at "
            "WHERE excluded.version > schema_version.version",
            (self.SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def get_soak_state(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM soak_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def prepare_qualification(self, *, collector: str, execution_id: str,
                              material: QualificationMaterial,
                              provenance: ExecutionProvenance | str | None,
                              started_at: datetime) -> QualificationEpoch:
        """Return the active qualification epoch, appending a reset when needed.

        This is called after the execution UUID/provenance exists and before
        the runner reads any prior healthy catalogue. The reset event is
        durable and linked to the execution UUID; terminal evidence is written
        separately after the run finishes.
        """
        trigger = normalize_provenance(provenance)
        started_iso = started_at.isoformat()
        components_json = _json(material.components())
        with self.connection:
            current = self.connection.execute(
                "SELECT * FROM qualification_epochs WHERE collector=? ORDER BY id DESC LIMIT 1",
                (collector,),
            ).fetchone()
            if current is not None and current["material_identity"] == material.identity():
                return self._qualification_epoch_from_row(current)
            previous_epoch_id = current["epoch_id"] if current is not None else None
            previous_material_identity = current["material_identity"] if current is not None else None
            reason = "INITIAL_MATERIAL_IDENTITY" if current is None else "MATERIAL_IDENTITY_CHANGED"
            epoch_id = new_epoch_id(collector)
            self.connection.execute(
                "INSERT INTO qualification_epochs("
                "collector,epoch_id,material_identity,material_components_json,started_at,"
                "created_by_execution_id,provenance,reason,previous_epoch_id,previous_material_identity"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (collector, epoch_id, material.identity(), components_json, started_iso,
                 execution_id, trigger.value, reason, previous_epoch_id, previous_material_identity),
            )
            event_type = "EPOCH_STARTED" if current is None else "RESET"
            self.connection.execute(
                "INSERT INTO qualification_events("
                "collector,event_type,execution_id,epoch_id,material_identity,"
                "material_components_json,provenance,healthy,reason,previous_epoch_id,"
                "previous_material_identity,occurred_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (collector, event_type, execution_id, epoch_id, material.identity(),
                 components_json, trigger.value, None, reason, previous_epoch_id,
                 previous_material_identity, started_iso),
            )
            row = self.connection.execute(
                "SELECT * FROM qualification_epochs WHERE collector=? AND epoch_id=?",
                (collector, epoch_id),
            ).fetchone()
            assert row is not None
            return self._qualification_epoch_from_row(row)

    @staticmethod
    def _qualification_epoch_from_row(row: sqlite3.Row) -> QualificationEpoch:
        return QualificationEpoch(
            collector=row["collector"],
            epoch_id=row["epoch_id"],
            material_identity=row["material_identity"],
            started_at=row["started_at"],
            execution_id=row["created_by_execution_id"],
            provenance=normalize_provenance(row["provenance"]),
            reason=row["reason"],
            previous_epoch_id=row["previous_epoch_id"],
            previous_material_identity=row["previous_material_identity"],
        )

    def record_qualification_terminal(self, *, collector: str, execution_id: str,
                                      epoch_id: str, material: QualificationMaterial,
                                      provenance: ExecutionProvenance | str | None,
                                      healthy: bool, finished_at: datetime) -> None:
        """Persist terminal qualification evidence independently and idempotently."""
        trigger = normalize_provenance(provenance)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO qualification_events("
                "collector,event_type,execution_id,epoch_id,material_identity,"
                "material_components_json,provenance,healthy,reason,previous_epoch_id,"
                "previous_material_identity,occurred_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (collector, "TERMINAL", execution_id, epoch_id, material.identity(),
                 _json(material.components()), trigger.value, int(healthy),
                 "HEALTHY" if healthy else "FAILED", None, None, finished_at.isoformat()),
            )

    def qualification_gate(self, collector: str, *, epoch_id: str | None = None,
                           material_identity: str | None = None) -> QualificationGate:
        """Read the durable gate decision without inventing missing provenance."""
        current = self.connection.execute(
            "SELECT * FROM qualification_epochs WHERE collector=? ORDER BY id DESC LIMIT 1",
            (collector,),
        ).fetchone()
        if current is None:
            return QualificationGate(collector, None, material_identity, False, "NO_ACTIVE_EPOCH")
        active_epoch = current["epoch_id"]
        active_material = current["material_identity"]
        if epoch_id is not None and epoch_id != active_epoch:
            return QualificationGate(collector, active_epoch, active_material, False, "STALE_EPOCH")
        if material_identity is not None and material_identity != active_material:
            return QualificationGate(collector, active_epoch, active_material, False, "MATERIAL_IDENTITY_MISMATCH")
        terminal = self.connection.execute(
            "SELECT execution_id,provenance,healthy,material_identity,material_components_json FROM qualification_events "
            "WHERE collector=? AND event_type='TERMINAL' AND epoch_id=? ORDER BY id DESC LIMIT 1",
            (collector, active_epoch),
        ).fetchone()
        if terminal is None:
            return QualificationGate(collector, active_epoch, active_material, False, "NO_TERMINAL_EVIDENCE")
        trigger = normalize_provenance(terminal["provenance"])
        if trigger is ExecutionProvenance.UNKNOWN:
            return QualificationGate(collector, active_epoch, active_material, False, "UNKNOWN_PROVENANCE",
                                     terminal["execution_id"])
        try:
            components = json.loads(terminal["material_components_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return QualificationGate(collector, active_epoch, active_material, False,
                                     "MALFORMED_MATERIAL_IDENTITY", terminal["execution_id"])
        if not (
            components.get("app_version")
            and components.get("config_fingerprint")
            and components.get("git_revision")
            and str(components["git_revision"]).lower() != "unknown"
        ):
            return QualificationGate(collector, active_epoch, active_material, False,
                                     "UNTRUSTWORTHY_MATERIAL_IDENTITY", terminal["execution_id"])
        if not terminal["healthy"]:
            return QualificationGate(collector, active_epoch, active_material, False, "LATEST_EXECUTION_UNHEALTHY",
                                     terminal["execution_id"])
        if terminal["material_identity"] != active_material:
            return QualificationGate(collector, active_epoch, active_material, False,
                                     "TERMINAL_MATERIAL_IDENTITY_MISMATCH", terminal["execution_id"])
        return QualificationGate(collector, active_epoch, active_material, True, "QUALIFIED",
                                 terminal["execution_id"])

    def qualification_events(self, collector: str | None = None) -> tuple[dict[str, Any], ...]:
        query = "SELECT * FROM qualification_events"
        params: tuple[object, ...] = ()
        if collector is not None:
            query += " WHERE collector=?"
            params = (collector,)
        query += " ORDER BY id"
        return tuple(dict(row) for row in self.connection.execute(query, params).fetchall())



    def set_soak_state(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO soak_state(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
            )

    def save_host_migration(self, migration: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO soak_host_migrations(from_host_id,to_host_id,recorded_at,observation_gap_seconds) "
                "VALUES(?,?,?,?)",
                (migration["from_host_id"], migration["to_host_id"], migration["recorded_at"],
                 migration["observation_gap_seconds"]),
            )

    def backup_to(self, destination: Path | str) -> Path:
        """Create a transactionally consistent, single-file transferable backup."""
        target = Path(destination).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".partial")
        if temporary.exists():
            temporary.unlink()
        try:
            backup = sqlite3.connect(temporary)
            try:
                self.connection.backup(backup)
            finally:
                backup.close()
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

    def last_healthy_catalogue(self, collector: str, *, qualification_epoch_id: str | None = None) -> dict[str, Observation]:
        query = "SELECT id FROM runs WHERE collector=? AND healthy=1"
        params: list[object] = [collector]
        if qualification_epoch_id is not None:
            query += " AND qualification_epoch_id=?"
            params.append(qualification_epoch_id)
        query += " ORDER BY id DESC LIMIT 1"
        run = self.connection.execute(query, params).fetchone()
        if run is None:
            return {}
        rows = self.connection.execute(
            "SELECT data_json FROM observations WHERE run_id=? ORDER BY identity", (run["id"],)
        ).fetchall()
        return {item.identity: item for item in (self._decode_observation(row["data_json"]) for row in rows)}

    def has_healthy_run(self, collector: str, *, qualification_epoch_id: str | None = None) -> bool:
        query = "SELECT 1 FROM runs WHERE collector=? AND healthy=1"
        params: list[object] = [collector]
        if qualification_epoch_id is not None:
            query += " AND qualification_epoch_id=?"
            params.append(qualification_epoch_id)
        query += " LIMIT 1"
        return self.connection.execute(query, params).fetchone() is not None

    def save_run(self, *, collector: str, started_at: datetime, finished_at: datetime, healthy: bool,
                 observations: tuple[Observation, ...], discoveries: list[Discovery],
                 warning: str | None, error: str | None, metadata: dict[str, Any] | None = None,
                 run_uuid: str | None = None, app_version: str | None = None,
                 schema_version_at_run: int | None = None, config_fingerprint: str | None = None,
                 git_revision: str | None = None, execution_provenance: ExecutionProvenance | str | None = None,
                 qualification_epoch_id: str | None = None, material_identity: str | None = None) -> int:
        trigger = normalize_provenance(execution_provenance)
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO runs(collector,started_at,finished_at,healthy,observation_count,warning,error,"
                "metadata_json,discovery_count,run_uuid,app_version,schema_version_at_run,config_fingerprint,"
                "git_revision,execution_provenance,qualification_epoch_id,material_identity) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (collector, started_at.isoformat(), finished_at.isoformat(), int(healthy), len(observations), warning, error,
                 _json(metadata or {}), len(discoveries), run_uuid, app_version,
                 schema_version_at_run, config_fingerprint, git_revision, trigger.value,
                 qualification_epoch_id, material_identity),
            )
            run_id = int(cursor.lastrowid)
            if healthy:
                for item in observations:
                    self.connection.execute(
                        "INSERT INTO observations(run_id,collector,identity,observed_at,source_url,data_json) VALUES(?,?,?,?,?,?)",
                        (run_id, collector, item.identity, item.observed_at.isoformat(), item.source_url, _json(asdict(item))),
                    )
                for item in discoveries:
                    self.connection.execute(
                        "INSERT INTO discoveries(run_id,collector,identity,change_type,confidence,editorial_level,source_url,discovered_at,previous_json,current_json,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, collector, item.identity, item.change_type.value, item.confidence.value,
                         item.editorial_level.value, item.source_url, item.discovered_at.isoformat(),
                         _json(item.previous) if item.previous is not None else None,
                         _json(item.current) if item.current is not None else None, _json(item.evidence)),
                    )
            return run_id

    def save_health(self, record: HealthRecord) -> None:
        with self.connection:
            self.connection.execute("""
                INSERT INTO collector_health(collector,healthy,observed_count,previous_count,warning,error,checked_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(collector) DO UPDATE SET
                healthy=excluded.healthy, observed_count=excluded.observed_count,
                previous_count=excluded.previous_count, warning=excluded.warning,
                error=excluded.error, checked_at=excluded.checked_at
            """, (record.collector, int(record.healthy), record.observed_count, record.previous_count,
                    record.warning, record.error, record.checked_at.isoformat()))

    def record_evidence(self, *, oem: str, source_class: str, identity: str, observed_at: datetime,
                         region: str | None = None, confidence: str | None = None,
                         editorial_level: str | None = None, source_url: str | None = None,
                         payload: dict[str, Any] | None = None) -> int:
        """Upsert one evidence record, preserving `first_seen` across calls.

        Never overwrites an earlier `first_seen` (spec: independent
        first-seen per source class survives later, stronger evidence).
        """
        with self.connection:
            existing = self.connection.execute(
                "SELECT id FROM evidence_records WHERE source_class=? AND identity=?", (source_class, identity)
            ).fetchone()
            observed_iso = observed_at.isoformat()
            if existing is None:
                cursor = self.connection.execute(
                    "INSERT INTO evidence_records(oem,source_class,identity,region,confidence,editorial_level,"
                    "source_url,first_seen,last_seen,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (oem, source_class, identity, region, confidence, editorial_level, source_url,
                     observed_iso, observed_iso, _json(payload or {})),
                )
                return int(cursor.lastrowid)
            evidence_id = int(existing["id"])
            self.connection.execute(
                "UPDATE evidence_records SET oem=?, region=?, confidence=?, editorial_level=?, source_url=?, "
                "last_seen=?, payload_json=? WHERE id=?",
                (oem, region, confidence, editorial_level, source_url, observed_iso, _json(payload or {}), evidence_id),
            )
            return evidence_id

    def record_evidence_event(self, *, evidence_id: int, observed_at: datetime, event: str,
                               payload: dict[str, Any] | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO evidence_timeline(evidence_id,observed_at,event,payload_json) VALUES(?,?,?,?)",
                (evidence_id, observed_at.isoformat(), event, _json(payload or {})),
            )

    def get_evidence(self, *, source_class: str, identity: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM evidence_records WHERE source_class=? AND identity=?", (source_class, identity)
        ).fetchone()
        return dict(row) if row else None

    def evidence_timeline(self, evidence_id: int) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT * FROM evidence_timeline WHERE evidence_id=? ORDER BY id", (evidence_id,)
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def schema_version(self) -> int:
        row = self.connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        return int(row["version"]) if row else 0

    def counts(self) -> dict[str, int]:
        return {table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("runs", "observations", "discoveries")}

    def latest_healthy_observations(self, collectors: tuple[str, ...]) -> tuple[Observation, ...]:
        items: list[Observation] = []
        for collector in collectors:
            items.extend(self.last_healthy_catalogue(collector).values())
        return tuple(sorted(items, key=lambda item: item.identity))

    @staticmethod
    def _decode_observation(raw: str) -> Observation:
        data = json.loads(raw)
        data["observed_at"] = datetime.fromisoformat(data["observed_at"])
        data["classification_evidence"] = tuple(data.get("classification_evidence", ()))
        return Observation(**data)
