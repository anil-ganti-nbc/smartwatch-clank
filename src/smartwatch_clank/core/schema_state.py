"""Persistent-state compatibility for the Smartwatch primary store (M18).

STD-DEPLOY-COM-002, family ADDITIVE_SCHEMA_MARKER_COMPATIBILITY: the store
has always carried a durable, monotonic `schema_version` marker (a single
row, `id = 1`), and its migrations are additive-only (CREATE TABLE IF NOT
EXISTS plus guarded ALTER TABLE). What it never had was a compatibility
decision: `_migrate()` ran unconditionally on every read-write open —
layering the full current schema over whatever it found, patching missing
columns, and stamping the marker upward — so ordinary construction could
mutate state before any compatibility was known, silently tolerate a newer
database (the monotonic stamp simply declined to move, and nothing failed),
and launder a marker-less existing database toward "current".

This module adds the missing decision. `inspect_schema` adjudicates one
open connection against the expected contract and never mutates:

- the `schema_version` marker is the durable authority, corroborated
  structurally: a marker claiming v3 must coexist with every expected
  table and the complete `runs` column set, else the state is PARTIAL
  (`MARKER_PRESENT != COMPATIBLE`; `STRUCTURE_EXISTS != COMPATIBLE`).
- a marker-less database with application tables is UNKNOWN: it is not
  fresh, must not be bootstrapped, and must not be stamped current.
- a marker reporting a version newer than this software understands is
  INCOMPATIBLE_NEWER (skew contract: FORWARD_ONLY_EXPLICIT — additive
  migrations do not prove backward compatibility, and no downgrade path
  exists or is claimed).

Scope of the structural contract, honestly stated: verified are required
table presence and the complete `runs` column set (the only table the
migration mechanism has ever ALTERed). Not claimed: column types,
nullability, indexes, or byte-for-byte DDL equivalence — the goal is to
detect meaningful compatibility contradictions, not to reproduce SQLite
serialization.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# The expected persistent-state contract of THIS software version. Single
# source of truth; `SQLiteStore.SCHEMA_VERSION` re-exports it so the schema,
# the migration stamp, and the compatibility gate cannot drift apart.
EXPECTED_SCHEMA_VERSION = 3

SCHEMA_VERSION_TABLE = "schema_version"

# Every table the current schema creates (the _migrate DDL at v3), including
# the marker itself. The OPS-COM-003 qualification tables share this store
# and are part of the same contract.
EXPECTED_TABLES: frozenset[str] = frozenset({
    "runs", "observations", "discoveries", "collector_health", "feedback",
    "samsung_reconciliation_runs", "samsung_reconciliation_records",
    "source_onboarding", "prelaunch_candidates", "samsung_candidate_events",
    "soak_state", "soak_host_migrations", SCHEMA_VERSION_TABLE,
    "evidence_records", "evidence_timeline", "qualification_epochs",
    "qualification_events",
})

# Required columns of `runs` — the only table the additive migration
# mechanism has ever ALTERed. Original v1 columns plus every guarded
# ADD COLUMN the store has shipped.
REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "runs": frozenset({
        "id", "collector", "started_at", "finished_at", "healthy",
        "observation_count", "warning", "error", "metadata_json",
        "discovery_count", "run_uuid", "app_version",
        "schema_version_at_run", "config_fingerprint", "git_revision",
        "execution_provenance", "qualification_epoch_id", "material_identity",
    }),
}


# Historical (pre-marker) generations of the store. The schema_version
# table itself was introduced during the Expansion Stage; a database from
# before that point is genuinely marker-less, and recognizing its exact
# historical shape is what allows the canonical additive migration to
# remain reachable for real old state. Each generation maps to the marker
# version that generation corresponds to.
LEGACY_GENERATIONS: tuple[dict[str, frozenset[str]], ...] = (
    {  # v1: original observation-history schema
        "runs": frozenset({
            "id", "collector", "started_at", "finished_at", "healthy",
            "observation_count", "warning", "error",
        }),
    },
    {  # v2: + soak metadata columns on runs
        "runs": frozenset({
            "id", "collector", "started_at", "finished_at", "healthy",
            "observation_count", "warning", "error", "metadata_json",
            "discovery_count",
        }),
    },
)


def _recognized_legacy_generation(tables: set[str], con: sqlite3.Connection):
    """Return (version, evidence) when `tables` is exactly one recognized
    historical generation at its exact column set; None otherwise."""
    if tables != {"runs"}:
        return None
    actual = frozenset(row[1] for row in con.execute("PRAGMA table_info(runs)"))
    for version, shape in enumerate(LEGACY_GENERATIONS, start=1):
        required = shape["runs"]
        if actual == required or (required <= actual and not (actual - required)):
            if actual == required:
                return version, {"legacy_generation": version, "runs_columns": sorted(actual)}
    return None


class SchemaState(str, Enum):
    """Adjudication verdicts. FRESH != UNKNOWN; MARKER_PRESENT !=
    COMPATIBLE; DB_OPENED != COMPATIBLE; MIGRATION_CAN_RUN != COMPATIBLE."""

    FRESH = "FRESH"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE_NEWER = "INCOMPATIBLE_NEWER"
    UNKNOWN = "UNKNOWN"
    CORRUPT = "CORRUPT"
    PARTIAL = "PARTIAL"


# Verdicts that can never participate in normal work.
UNADMITTABLE_STATES = frozenset({
    SchemaState.INCOMPATIBLE_NEWER,
    SchemaState.UNKNOWN,
    SchemaState.CORRUPT,
    SchemaState.PARTIAL,
})


@dataclass(frozen=True)
class SchemaStateReport:
    """Read-only verdict on one persistent store, with the evidence that
    produced it. `as_evidence()` is the machine-readable refusal record."""

    state: SchemaState
    expected_version: int
    observed_version: int | None
    reason: str
    evidence: dict = field(default_factory=dict)

    def as_evidence(self) -> dict:
        return {
            "compatibility_state": self.state.value,
            "expected_schema_version": self.expected_version,
            "observed_schema_version": self.observed_version,
            "reason": self.reason,
            **self.evidence,
        }

    def __str__(self) -> str:
        return (
            f"{self.state.value}: {self.reason} "
            f"(expected schema v{self.expected_version}, "
            f"observed {'none' if self.observed_version is None else f'v{self.observed_version}'})"
        )


class SchemaStateError(RuntimeError):
    """Raised when a store is refused because its persistent state is not
    compatible with this software. `.report` carries the full read-only
    evidence; the database was not mutated by the refusal."""

    def __init__(self, report: SchemaStateReport) -> None:
        super().__init__(
            "persistent-state compatibility refused: "
            f"{report} — normal work was not admitted; the database was "
            f"left untouched for diagnosis"
        )
        self.report = report


def _verdict(state, expected_version, observed_version, reason, **evidence):
    return SchemaStateReport(
        state=state, expected_version=expected_version,
        observed_version=observed_version, reason=reason, evidence=evidence,
    )


def inspect_schema(
    con: sqlite3.Connection, *, expected_version: int = EXPECTED_SCHEMA_VERSION
) -> SchemaStateReport:
    """Adjudicate one open SQLite connection against the expected contract.
    Strictly read-only: never creates, alters, stamps, or repairs."""
    try:
        quick = con.execute("PRAGMA quick_check").fetchone()[0]
        if quick != "ok":
            return _verdict(
                SchemaState.CORRUPT, expected_version, None,
                f"quick_check reported {quick!r}", quick_check=str(quick),
            )
        tables = {
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
    except sqlite3.DatabaseError as exc:
        return _verdict(
            SchemaState.CORRUPT, expected_version, None,
            f"not a usable SQLite database: {exc}", sqlite_error=str(exc),
        )

    if not tables:
        return _verdict(
            SchemaState.FRESH, expected_version, None,
            "no persistent state yet (zero user tables); canonical bootstrap "
            "may create it",
            user_tables=[],
        )

    if SCHEMA_VERSION_TABLE not in tables:
        legacy = _recognized_legacy_generation(tables, con)
        if legacy is not None:
            version, evidence = legacy
            return _verdict(
                SchemaState.MIGRATION_REQUIRED, expected_version, version,
                f"recognized historical pre-marker generation (v{version}): "
                "canonical additive migration to the current contract",
                **evidence,
                user_tables=sorted(tables),
            )
        return _verdict(
            SchemaState.UNKNOWN, expected_version, None,
            f"existing database has {len(tables)} table(s) but no "
            f"{SCHEMA_VERSION_TABLE} authority; it is not fresh and must "
            "not be bootstrapped or stamped",
            user_tables=sorted(tables),
        )

    marker_columns = {row[1] for row in con.execute(f"PRAGMA table_info({SCHEMA_VERSION_TABLE})")}
    if not {"id", "version"} <= marker_columns:
        return _verdict(
            SchemaState.UNKNOWN, expected_version, None,
            f"{SCHEMA_VERSION_TABLE} exists but lacks the expected id/version "
            "shape; the authority is unreadable",
            user_tables=sorted(tables),
        )
    raw = [row[0] for row in con.execute(f"SELECT version FROM {SCHEMA_VERSION_TABLE}")]
    versions: list[int] = []
    for value in raw:
        if isinstance(value, int) and not isinstance(value, bool):
            versions.append(value)
        elif isinstance(value, str):
            try:
                versions.append(int(value))
            except ValueError:
                return _verdict(
                    SchemaState.UNKNOWN, expected_version, None,
                    f"{SCHEMA_VERSION_TABLE} contains non-integer version data "
                    f"({raw!r}); the authority is corrupt",
                    user_tables=sorted(tables),
                )
        else:
            return _verdict(
                SchemaState.UNKNOWN, expected_version, None,
                f"{SCHEMA_VERSION_TABLE} contains non-integer version data "
                f"({raw!r}); the authority is corrupt",
                user_tables=sorted(tables),
            )
    if not versions:
        return _verdict(
            SchemaState.UNKNOWN, expected_version, 0,
            f"{SCHEMA_VERSION_TABLE} exists but records no version; state is "
            "neither fresh nor versioned",
            user_tables=sorted(tables),
        )

    observed = max(versions)
    if observed > expected_version:
        return _verdict(
            SchemaState.INCOMPATIBLE_NEWER, expected_version, observed,
            f"persistent state is newer (v{observed}) than this software "
            f"understands (v{expected_version}); the skew contract is "
            "FORWARD_ONLY_EXPLICIT and older software must not open it",
            user_tables=sorted(tables),
        )
    if observed < expected_version:
        return _verdict(
            SchemaState.MIGRATION_REQUIRED, expected_version, observed,
            f"older marked state (v{observed}) must migrate through the "
            f"canonical additive mechanism to v{expected_version} before "
            "normal work",
            user_tables=sorted(tables),
        )

    missing_tables = sorted(EXPECTED_TABLES - tables)
    missing_columns: dict[str, list[str]] = {}
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        actual = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        absent = sorted(required - actual)
        if absent:
            missing_columns[table] = absent
    if missing_tables or missing_columns:
        return _verdict(
            SchemaState.PARTIAL, expected_version, observed,
            f"marker records v{observed} but the structural contract is not "
            f"met: {len(missing_tables)} table(s) missing, "
            f"{len(missing_columns)} table(s) missing required columns",
            missing_tables=missing_tables,
            tables_missing_columns=missing_columns,
            user_tables=sorted(tables),
        )

    return _verdict(
        SchemaState.COMPATIBLE, expected_version, observed,
        f"state matches the expected v{expected_version} contract "
        f"({len(EXPECTED_TABLES)} tables incl. {SCHEMA_VERSION_TABLE}, "
        "required columns verified)",
        user_tables=sorted(tables),
    )


def inspect_store(path) -> SchemaStateReport:
    """Read-only inspection of a primary store by path. A missing file is
    FRESH. Opens its own mode=ro handle and never mutates."""
    if not isinstance(path, Path):
        path = Path(path)
    if not path.exists():
        return _verdict(
            SchemaState.FRESH, EXPECTED_SCHEMA_VERSION, None,
            "database file absent; canonical bootstrap may create it",
        )
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return inspect_schema(con)
    finally:
        con.close()
