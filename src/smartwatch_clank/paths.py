from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(os.environ.get("SMARTWATCH_CLANK_CONFIG_ROOT", Path(__file__).resolve().parents[2]))


def default_database_path() -> Path:
    configured = os.environ.get("SMARTWATCH_CLANK_DB")
    return Path(configured) if configured else PROJECT_ROOT / "var" / "smartwatch-clank.sqlite3"


def config_path(name: str) -> Path:
    return PROJECT_ROOT / "config" / name
