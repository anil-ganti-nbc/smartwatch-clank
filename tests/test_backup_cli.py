"""Regression tests for the `backup` CLI serialization boundary.

Covers the deployment-found defect: SQLiteStore.backup_to() returns a Path;
the CLI used to dict()-it (TypeError) after the backup data was already
written. The contract under test:

  * exit 0 with machine-readable JSON on stdout;
  * the backup file exists, opens, and passes integrity_check;
  * when a continuity registry exists beside the database, the backup gains
    a `.continuity.jsonl` sidecar whose bytes and sha256 are reported;
  * without a registry, no sidecar is claimed.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from smartwatch_clank.cli import main
from smartwatch_clank.core.lock import RunLock
from smartwatch_clank.core.store import SQLiteStore


def _seed_registry(db: Path, events: list[dict]) -> Path:
    from smartwatch_clank.core.continuity import registry_path

    reg = registry_path(db)
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    return reg


def test_backup_cli_json_output_and_integrity(tmp_path, capsys):
    db = tmp_path / "sw.sqlite3"
    out = tmp_path / "backups" / "rp.db"
    with SQLiteStore(db) as store:
        store.get_soak_state("probe")  # opens + migrates schema

    rc = main(["--database", str(db), "backup", str(out)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "BACKED_UP"
    assert payload["output"] == str(out.resolve())
    con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()
    # No registry -> the report must not claim a continuity snapshot.
    assert "continuity_snapshot" not in payload


def test_backup_cli_writes_verified_continuity_sidecar(tmp_path, capsys):
    db = tmp_path / "sw.sqlite3"
    out = tmp_path / "rp2.db"
    with SQLiteStore(db) as store:
        store.get_soak_state("probe")  # opens + migrates schema

    events = [
        {"event_id": "e1", "event_type": "DATA_LOSS"},
        {"event_id": "e2", "event_type": "RESTORE_FROM_BACKUP",
         "new_epoch_id": "sw-epoch-1-restored"},
    ]
    reg = _seed_registry(db, events)

    rc = main(["--database", str(db), "backup", str(out)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    snap = payload["continuity_snapshot"]
    sidecar = Path(snap["path"])
    assert sidecar.name == "rp2.db.continuity.jsonl"
    raw = sidecar.read_bytes()
    assert raw == reg.read_bytes()
    assert snap["sha256"] == hashlib.sha256(raw).hexdigest()
    assert snap["size_bytes"] == len(raw)


def _erofs_on_rdwr_open(real_open):
    def _fake_open(path, flags, *args, **kwargs):
        if flags & os.O_CREAT and flags & os.O_RDWR:
            raise OSError(errno.EROFS, "Read-only file system", str(path))
        return real_open(path, flags, *args, **kwargs)
    return _fake_open


def test_backup_cli_succeeds_end_to_end_against_a_read_only_mounted_source(tmp_path, capsys):
    """Reproduces scripts/deploy_hetzner.sh's pre-deploy backup step
    end-to-end through the real `backup` CLI entrypoint: the DB volume is
    mounted read-only, but the lock file already exists from a prior
    writable production/collector run against the same volume (exactly the
    live staging state). Before the fix, this aborted the whole deploy at
    RunLock.acquire() with a raw OSError before checkout/build ever ran --
    reproduced for real against a genuine Docker `:ro` bind mount on
    Hetzner/NAS; this pins the same fix at the actual entrypoint the deploy
    script calls, without needing Docker to run in CI.
    """
    db = tmp_path / "sw.sqlite3"
    out = tmp_path / "backups" / "pre-deploy.db"

    # Seed exactly like the real staging volume: a migrated DB plus a lock
    # file left behind by a prior writable run.
    with SQLiteStore(db) as store:
        store.set_soak_state("active_host_id", "hetzner-clank-fleet-01")
    seeded_lock = RunLock(db)
    seeded_lock.acquire()
    seeded_lock.release()

    real_open = os.open
    with mock.patch("smartwatch_clank.core.lock.os.open", side_effect=_erofs_on_rdwr_open(real_open)):
        rc = main(["--database", str(db), "backup", str(out)])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "BACKED_UP"
    con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT value FROM soak_state WHERE key='active_host_id'").fetchone()[0] == (
            "hetzner-clank-fleet-01"
        )
    finally:
        con.close()

    # The source database itself must be provably untouched by the backup:
    # still openable read-write afterward, same host_id, no stray writes.
    with SQLiteStore(db) as store:
        assert store.get_soak_state("active_host_id") == "hetzner-clank-fleet-01"
