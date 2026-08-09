from __future__ import annotations

import platform
import sys
from dataclasses import asdict, dataclass

from . import __version__


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    service: str
    version: str
    python: str
    platform: str
    stage: int
    live_collectors_enabled: bool
    notifications_enabled: bool


def identity() -> dict[str, object]:
    return asdict(RuntimeIdentity("smartwatch-clank", __version__, sys.version.split()[0], platform.platform(), 2, True, False))


def health(store) -> dict[str, object]:
    rows = store.connection.execute(
        "SELECT collector,healthy,observed_count,previous_count,warning,error,checked_at FROM collector_health ORDER BY collector"
    ).fetchall()
    collectors = [dict(row) for row in rows]
    return {"status": "healthy" if all(row["healthy"] for row in collectors) else "degraded",
            "version": __version__, "collectors": collectors}
