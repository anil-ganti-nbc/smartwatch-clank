"""Separate, on-disk QC/decision archive database.

Modeled directly on korean-tech-wire's `storage/qc_archive.py` pattern (a
physically separate SQLite file, never another table in the live collector
database) and on the same fleet convention chinese-tech-wire's LeadOutcome
established: an append-style, provenance-carrying record of what a local
operator decided about one discovery.

Why a separate file rather than a table in `smartwatch-clank.sqlite3`:

  - The live database's schema is fleet-governed and additive-only (see
    `SQLiteStore.SCHEMA_VERSION`'s docstring); a QC decision ledger evolving
    on its own cadence should never require a migration to the production
    observation history.
  - A QC decision is a durable editorial/audit record. Storing a full
    snapshot of the discovery (collector, identity, change type, evidence,
    previous/current payload, source URL, discovered_at) at decision time
    means the archive stays self-contained and readable even if the live
    `discoveries` row is later pruned by some future retention policy.
  - UNIQUE(discovery_id) is the race guard: two near-simultaneous QC
    submissions for the same discovery can both attempt an INSERT, but only
    one commits. The loser raises `AlreadyDecided` and the caller reports a
    graceful "already decided" response, never an unhandled exception or a
    duplicate archive row.

"Active queue" filtering (removing a QC'd discovery from the default
dashboard view) is done by the caller consulting `decided_discovery_ids()`
-- the live database's `discoveries` table is never mutated or deleted by a
QC decision, so evidence is never destroyed, and a restart is safe because
this ledger is on-disk SQLite, not in-memory state.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The four QC actions specified for Smartwatch Clank. OUT_OF_STOCK is the
# e-commerce-flavoured equivalent of a terminal "this is no longer live"
# verdict for a merchandising/support discovery -- distinct from
# FALSE_POSITIVE (the discovery was never real evidence in the first place).
QC_DECISIONS = ("USEFUL", "NOT_USEFUL", "FALSE_POSITIVE", "OUT_OF_STOCK")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS qc_decisions (
    id INTEGER PRIMARY KEY,
    discovery_id INTEGER NOT NULL UNIQUE,
    run_id INTEGER,
    collector TEXT NOT NULL,
    identity TEXT NOT NULL,
    change_type TEXT,
    confidence TEXT,
    editorial_level TEXT,
    source_url TEXT,
    discovered_at TEXT,
    previous_json TEXT,
    current_json TEXT,
    evidence_json TEXT,
    decision TEXT NOT NULL,
    note TEXT,
    decided_at TEXT NOT NULL,
    decided_by TEXT
);
CREATE INDEX IF NOT EXISTS qc_decisions_decided_at_idx ON qc_decisions(decided_at DESC);
CREATE INDEX IF NOT EXISTS qc_decisions_collector_idx ON qc_decisions(collector);
"""


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


class AlreadyDecided(Exception):
    """Raised when a discovery already has a QC decision (race or re-submit)."""


class QCArchive:
    """A separate, append-only ledger of local-operator QC decisions."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def migrate(self) -> None:
        with self.connect() as con:
            con.executescript(_SCHEMA)

    def decided_discovery_ids(self) -> set[int]:
        with self.connect() as con:
            return {row[0] for row in con.execute("SELECT discovery_id FROM qc_decisions")}

    def decision_for(self, discovery_id: int) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM qc_decisions WHERE discovery_id=?", (discovery_id,)).fetchone()
        return dict(row) if row else None

    def decide(self, discovery: dict[str, Any], decision: str, *, note: str | None = None,
               decided_by: str = "local_operator") -> None:
        """Transactionally archive one discovery's full snapshot + provenance
        and record the decision. Raises AlreadyDecided if this discovery_id
        already has a row (unique-constraint race guard -- never a silent
        duplicate write, and the live `discoveries` row is never touched)."""
        if decision not in QC_DECISIONS:
            raise ValueError(f"unknown QC decision: {decision!r}")
        try:
            with self.connect() as con:
                con.execute(
                    "INSERT INTO qc_decisions(discovery_id,run_id,collector,identity,change_type,confidence,"
                    "editorial_level,source_url,discovered_at,previous_json,current_json,evidence_json,"
                    "decision,note,decided_at,decided_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        discovery["id"], discovery.get("run_id"), discovery["collector"], discovery["identity"],
                        discovery.get("change_type"), discovery.get("confidence"), discovery.get("editorial_level"),
                        discovery.get("source_url"), discovery.get("discovered_at"),
                        discovery.get("previous_json"), discovery.get("current_json"), discovery.get("evidence_json"),
                        decision, note, _iso(), decided_by,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise AlreadyDecided(f"discovery {discovery['id']} already has a QC decision") from error

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM qc_decisions ORDER BY decided_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def status(self) -> dict[str, int]:
        with self.connect() as con:
            total = con.execute("SELECT COUNT(*) FROM qc_decisions").fetchone()[0]
            by_decision = {row["decision"]: row["n"] for row in con.execute(
                "SELECT decision, COUNT(*) AS n FROM qc_decisions GROUP BY decision"
            )}
        return {"total": total, **by_decision}
