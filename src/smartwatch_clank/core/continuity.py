"""Observational continuity registry (ADR-0006) for Smartwatch Clank.

Append-only JSONL registry in RUNTIME state
(`<db parent>/continuity/continuity-events.jsonl`), never inside the
database and never inside the source tree. Every record is content-hashed;
records are never edited or removed — later knowledge appends.

Seed events record operator-verified incident facts ONLY. On 2026-08-23 a
destructive volume deletion destroyed the live database; the lane was
restored from the 2026-08-18T20:50:37Z backup and became authoritative at
2026-08-23T22:09Z, serving history through approximately 2026-08-18T20:13Z.
Observations between those instants are lost and are NOT reconstructed;
the restored-history epoch is `sw-epoch-1-restored` (the canonical fleet
registry records a longer suffixed identifier whose full value is UNKNOWN
to this repository — recorded here exactly as far as it is evidenced).

Evidence basis:
- clank-architecture ADR-0006 (contract + vocabulary)
- clank-architecture DATA_SURVIVABILITY.md section 6 / 17.1 / 17.4
- diagnostic-clank fleet.yaml: smartwatch-hetzner-cron-lane-01 (staging)
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

CLANK_ID = "smartwatch-clank"
INSTANCE_ID = "smartwatch-hetzner-cron-lane-01"
LANE_ID = "staging"
EPOCH_ID = "sw-epoch-1-restored"

RESTORE_AUTHORITATIVE_AT_UTC = "2026-08-23T22:09:00Z"
BACKUP_TAKEN_AT_UTC = "2026-08-18T20:50:37Z"
LAST_PRE_LOSS_HISTORY_AT_UTC = "2026-08-18T20:13:00Z"  # canon marks this instant approximate

SEED_EVENTS: tuple[dict, ...] = (
    {
        "event_id": "sw-20260823-volume-loss-0001",
        "clank_id": CLANK_ID,
        "instance_id": INSTANCE_ID,
        "lane_id": LANE_ID,
        "event_type": "DATA_LOSS",
        "effective_start": RESTORE_AUTHORITATIVE_AT_UTC,
        "effective_end": None,
        "discovered_at": "2026-08-23T22:09:00Z",
        "evidence_refs": [
            "clank-architecture/adr/0006-continuity-and-epoch-semantics.md",
            "clank-architecture/DATA_SURVIVABILITY.md#6-smartwatch-case-analysis-post-incident-posture",
            "clank-architecture/DATA_SURVIVABILITY.md#17-pass-2-update",
        ],
        "previous_epoch_id": None,
        "new_epoch_id": None,
        "origin": "operator",
        "notes": (
            "Destructive volume deletion on 2026-08-23 destroyed the live "
            "database including all observations newer than the newest "
            "backup. The pre-loss epoch was never locally named; its "
            "identifier remains UNKNOWN to this repository."
        ),
    },
    {
        "event_id": "sw-20260823-restore-from-backup-0002",
        "clank_id": CLANK_ID,
        "instance_id": INSTANCE_ID,
        "lane_id": LANE_ID,
        "event_type": "RESTORE_FROM_BACKUP",
        "effective_start": RESTORE_AUTHORITATIVE_AT_UTC,
        "effective_end": None,
        "discovered_at": "2026-08-24T00:00:00Z",
        "evidence_refs": [
            "clank-architecture/DATA_SURVIVABILITY.md#6-smartwatch-case-analysis-post-incident-posture",
            "backup: smartwatch pre-stage-c backup 2026-08-18T205037Z (integrity ok)",
        ],
        "previous_epoch_id": None,
        "new_epoch_id": EPOCH_ID,
        "origin": "operator",
        "notes": (
            "Restored from the 2026-08-18T205037Z backup; authoritative from "
            f"{RESTORE_AUTHORITATIVE_AT_UTC}; serves history through "
            f"approximately {LAST_PRE_LOSS_HISTORY_AT_UTC} (canon marks the "
            "instant approximate). Restoration does NOT imply continuity."
        ),
    },
    {
        "event_id": "sw-20260818-observation-gap-0003",
        "clank_id": CLANK_ID,
        "instance_id": INSTANCE_ID,
        "lane_id": LANE_ID,
        "event_type": "OBSERVATION_GAP",
        "effective_start": LAST_PRE_LOSS_HISTORY_AT_UTC,
        "effective_end": RESTORE_AUTHORITATIVE_AT_UTC,
        "discovered_at": "2026-08-24T00:00:00Z",
        "evidence_refs": [
            "clank-architecture/DATA_SURVIVABILITY.md#6-smartwatch-case-analysis-post-incident-posture",
        ],
        "previous_epoch_id": EPOCH_ID,
        "new_epoch_id": EPOCH_ID,
        "origin": "operator",
        "notes": (
            "Known observation gap spanning the loss window "
            "(approximate start per canon). Absence inside this window is "
            "never zero and never novelty; post-gap source returns must be "
            "evaluated against restored history without backfilling."
        ),
    },
)

_REGISTRY_LOCK = threading.Lock()


def registry_path(db_path: str | Path) -> Path:
    return Path(db_path).resolve().parent / "continuity" / "continuity-events.jsonl"


def _content_hash(record: dict) -> str:
    payload = {k: v for k, v in record.items() if k != "content_hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_events(db_path: str | Path) -> list[dict]:
    path = registry_path(db_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_hashes(events: list[dict]) -> list[str]:
    bad: list[str] = []
    for event in events:
        expected = _content_hash(event)
        if event.get("content_hash") != expected:
            bad.append(event.get("event_id", "<unnamed>"))
    return bad


def append_event(db_path: str | Path, event: dict) -> dict:
    record = {
        "clank_id": CLANK_ID,
        "instance_id": INSTANCE_ID,
        "lane_id": LANE_ID,
        **event,
    }
    record["content_hash"] = _content_hash(record)
    path = registry_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _REGISTRY_LOCK:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


def ensure_registry(db_path: str | Path) -> Path:
    existing_ids = {e.get("event_id") for e in read_events(db_path)}
    for seed in SEED_EVENTS:
        if seed["event_id"] not in existing_ids:
            append_event(db_path, {k: v for k, v in seed.items()})
            existing_ids.add(seed["event_id"])
    return registry_path(db_path)
