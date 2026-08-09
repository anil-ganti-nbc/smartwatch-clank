from __future__ import annotations

import os
import platform
import socket
import uuid
from datetime import datetime, timezone
from typing import Any


def host_identity() -> dict[str, str]:
    hostname = socket.gethostname()
    return {
        "host_id": os.environ.get("SMARTWATCH_CLANK_HOST_ID", hostname).strip() or hostname,
        "hostname": hostname,
        "platform": platform.platform(),
    }


def prepare_soak_cycle(store, *, mode: str) -> dict[str, Any]:
    """Create portable host/cycle metadata shared by every run in an invocation."""
    now = datetime.now(timezone.utc)
    current = host_identity()
    previous_host = store.get_soak_state("active_host_id")
    previous_finished = store.connection.execute("SELECT MAX(finished_at) FROM runs").fetchone()[0]
    migration = None
    if previous_host and previous_host != current["host_id"]:
        gap_seconds = None
        if previous_finished:
            gap_seconds = max(0.0, (now - datetime.fromisoformat(previous_finished)).total_seconds())
        migration = {
            "from_host_id": previous_host,
            "to_host_id": current["host_id"],
            "recorded_at": now.isoformat(),
            "observation_gap_seconds": gap_seconds,
        }
        store.save_host_migration(migration)
    store.set_soak_state("active_host_id", current["host_id"])
    store.set_soak_state("active_hostname", current["hostname"])
    store.set_soak_state("last_cycle_started_at", now.isoformat())
    return {
        "cycle_id": uuid.uuid4().hex,
        "mode": mode,
        "started_at": now.isoformat(),
        "host_id": current["host_id"],
        "hostname": current["hostname"],
        "platform": current["platform"],
        "process_id": os.getpid(),
        "host_migration": migration,
    }
