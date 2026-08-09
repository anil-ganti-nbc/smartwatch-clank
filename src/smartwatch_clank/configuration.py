from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core.runner import RunnerConfig
from .paths import PROJECT_ROOT, config_path


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    runner: RunnerConfig
    production_allowlist: tuple[str, ...]
    database: Path
    sources: tuple[Path, ...]

    def provenance(self) -> dict[str, object]:
        return {
            "repository_defaults": str(self.sources[0]),
            "local_override": str(self.sources[1]) if len(self.sources) > 1 else None,
            "production_allowlist": list(self.production_allowlist),
            "database": str(self.database.resolve()),
        }


def load_runtime_config() -> RuntimeConfig:
    defaults_path = config_path("config.yaml")
    data = json.loads(defaults_path.read_text(encoding="utf-8"))
    sources = [defaults_path]
    local_path = config_path("local.yaml")
    if local_path.exists():
        data = _merge(data, json.loads(local_path.read_text(encoding="utf-8")))
        sources.append(local_path)
    runner = data.get("runner", {})
    database = Path(os.environ.get("SMARTWATCH_CLANK_DB", data.get("database", "var/smartwatch-clank.sqlite3")))
    if not database.is_absolute():
        database = PROJECT_ROOT / database
    return RuntimeConfig(
        RunnerConfig(
            unexpected_zero_is_failure=runner.get("unexpected_zero_is_failure", True),
            catalogue_warning_ratio=runner.get("catalogue_warning_ratio", 0.75),
            catalogue_failure_ratio=runner.get("catalogue_failure_ratio", 0.50),
        ),
        tuple(data.get("production_allowlist", ())), database, tuple(sources),
    )
