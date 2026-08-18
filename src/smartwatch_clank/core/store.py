from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Discovery, HealthRecord, Observation


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class SQLiteStore:
    # Bumped whenever a schema change is made. Additive-only (CREATE TABLE IF
    # NOT EXISTS / guarded ALTER TABLE) so historical data is never dropped;
    # see the Expansion Stage A report for what each version added.
    SCHEMA_VERSION = 2

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._migrate()

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
        """)
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(runs)")}
        if "metadata_json" not in columns:
            self.connection.execute("ALTER TABLE runs ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        if "discovery_count" not in columns:
            self.connection.execute("ALTER TABLE runs ADD COLUMN discovery_count INTEGER NOT NULL DEFAULT 0")
        for extra_column in ("run_uuid", "app_version", "schema_version_at_run", "config_fingerprint", "git_revision"):
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

    def last_healthy_catalogue(self, collector: str) -> dict[str, Observation]:
        run = self.connection.execute(
            "SELECT id FROM runs WHERE collector=? AND healthy=1 ORDER BY id DESC LIMIT 1", (collector,)
        ).fetchone()
        if run is None:
            return {}
        rows = self.connection.execute(
            "SELECT data_json FROM observations WHERE run_id=? ORDER BY identity", (run["id"],)
        ).fetchall()
        return {item.identity: item for item in (self._decode_observation(row["data_json"]) for row in rows)}

    def has_healthy_run(self, collector: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM runs WHERE collector=? AND healthy=1 LIMIT 1", (collector,)
        ).fetchone() is not None

    def save_run(self, *, collector: str, started_at: datetime, finished_at: datetime, healthy: bool,
                 observations: tuple[Observation, ...], discoveries: list[Discovery],
                 warning: str | None, error: str | None, metadata: dict[str, Any] | None = None,
                 run_uuid: str | None = None, app_version: str | None = None,
                 schema_version_at_run: int | None = None, config_fingerprint: str | None = None,
                 git_revision: str | None = None) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO runs(collector,started_at,finished_at,healthy,observation_count,warning,error,"
                "metadata_json,discovery_count,run_uuid,app_version,schema_version_at_run,config_fingerprint,"
                "git_revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (collector, started_at.isoformat(), finished_at.isoformat(), int(healthy), len(observations), warning, error,
                 _json(metadata or {}), len(discoveries), run_uuid, app_version,
                 schema_version_at_run, config_fingerprint, git_revision),
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
