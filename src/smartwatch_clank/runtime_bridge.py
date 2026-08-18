from __future__ import annotations

import os
import platform
import sys
from dataclasses import asdict, dataclass

from . import __version__
from .core.store import SQLiteStore


def _source_revision() -> str:
    """Full Git SHA the running image was built from, baked in at build time.

    Set via the Dockerfile's `GIT_REVISION` build arg ->
    `SMARTWATCH_CLANK_SOURCE_REVISION` env var, never read from a `.git`
    directory at runtime. Local/non-Docker runs report "unknown" rather than
    a fabricated value. Pattern proven on OEM Radar / Chinese Tech Wire /
    Feature Phone Clank.
    """
    return os.environ.get("SMARTWATCH_CLANK_SOURCE_REVISION", "unknown")


def _source_revision_short() -> str:
    revision = _source_revision()
    return revision if revision == "unknown" else revision[:12]


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    service: str
    version: str
    python: str
    platform: str
    stage: int
    live_collectors_enabled: bool
    notifications_enabled: bool
    source_revision: str
    source_revision_short: str
    schema_version: int


def identity() -> dict[str, object]:
    return asdict(RuntimeIdentity(
        "smartwatch-clank", __version__, sys.version.split()[0], platform.platform(), 2, True, False,
        _source_revision(), _source_revision_short(), SQLiteStore.SCHEMA_VERSION,
    ))


def health(store) -> dict[str, object]:
    rows = store.connection.execute(
        "SELECT collector,healthy,observed_count,previous_count,warning,error,checked_at FROM collector_health ORDER BY collector"
    ).fetchall()
    collectors = [dict(row) for row in rows]
    return {"status": "healthy" if all(row["healthy"] for row in collectors) else "degraded",
            "version": __version__, "collectors": collectors}
