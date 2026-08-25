"""Continuity registry honesty tests (ADR-0006).

The 2026-08-23 volume loss + restore must remain visible forever as
explicit evidence: DATA_LOSS, RESTORE_FROM_BACKUP, and a bounded
OBSERVATION_GAP. The lost observations are never reconstructed; records
are append-only and content-hashed.
"""

from __future__ import annotations

from pathlib import Path

from smartwatch_clank.core import continuity


def test_registry_seeds_operator_verified_incident_facts(tmp_path: Path):
    db = tmp_path / "sw.sqlite3"
    continuity.ensure_registry(db)
    events = continuity.read_events(db)
    types = sorted(e["event_type"] for e in events)
    assert types == ["DATA_LOSS", "OBSERVATION_GAP", "RESTORE_FROM_BACKUP"]

    restore = next(e for e in events if e["event_type"] == "RESTORE_FROM_BACKUP")
    gap = next(e for e in events if e["event_type"] == "OBSERVATION_GAP")
    loss = next(e for e in events if e["event_type"] == "DATA_LOSS")

    # Honesty invariants pinned to operator-verified canon.
    assert restore["new_epoch_id"] == "sw-epoch-1-restored"
    assert restore["effective_start"] == "2026-08-23T22:09:00Z"
    assert "2026-08-18T205037Z" in restore["notes"]
    assert loss["previous_epoch_id"] is None, "destroyed epoch was never named"
    assert gap["effective_start"] == "2026-08-18T20:13:00Z"
    assert gap["effective_end"] == "2026-08-23T22:09:00Z"


def test_seeding_is_idempotent_and_append_only(tmp_path: Path):
    db = tmp_path / "sw.sqlite3"
    first_path = continuity.ensure_registry(db)
    first = continuity.read_events(db)
    second_path = continuity.ensure_registry(db)
    assert first_path == second_path
    assert continuity.read_events(db) == first
    lines = first_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(first)


def test_records_are_tamper_evident(tmp_path: Path):
    db = tmp_path / "sw.sqlite3"
    continuity.ensure_registry(db)
    events = continuity.read_events(db)
    assert continuity.verify_hashes(events) == []
    raw = continuity.registry_path(db).read_text(encoding="utf-8")
    tampered = raw.replace("authoritative", "authoritative-tampered")
    assert tampered != raw
    continuity.registry_path(db).write_text(tampered, encoding="utf-8")
    events = continuity.read_events(db)
    mismatched = set(continuity.verify_hashes(events))
    assert mismatched, "the edited record must fail its content hash"
    assert "sw-20260823-restore-from-backup-0002" in mismatched
    # Untampered siblings stay verifiable - tampering is localized evidence.
    assert mismatched < {e["event_id"] for e in events}


def test_cli_continuity_command_round_trip(tmp_path: Path):
    from smartwatch_clank.cli import main

    db = tmp_path / "sw.sqlite3"
    assert main(["--database", str(db), "continuity"]) == 1  # absent -> NO_REGISTRY
    assert main(["--database", str(db), "continuity", "--ensure-seed"]) == 0
    assert main(["--database", str(db), "continuity"]) == 0
