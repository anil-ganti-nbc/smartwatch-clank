from __future__ import annotations

import hashlib
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
    config_fingerprint: str = ""

    def provenance(self) -> dict[str, object]:
        return {
            "repository_defaults": str(self.sources[0]),
            "local_override": str(self.sources[1]) if len(self.sources) > 1 else None,
            "production_allowlist": list(self.production_allowlist),
            "database": str(self.database.resolve()),
            "config_fingerprint": self.config_fingerprint,
        }


def _fingerprint(*payloads: dict[str, Any]) -> str:
    """Stable hash of loaded config content, independent of key order.

    Changes whenever `config.yaml`/`scope.yaml` content changes; stable
    otherwise. Used as run provenance so a discovery can be traced back to
    the exact configuration that produced it (spec: config fingerprint).
    """
    normalized = json.dumps(payloads, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def load_runtime_config() -> RuntimeConfig:
    defaults_path = config_path("config.yaml")
    data = json.loads(defaults_path.read_text(encoding="utf-8"))
    sources = [defaults_path]
    local_path = config_path("local.yaml")
    if local_path.exists():
        data = _merge(data, json.loads(local_path.read_text(encoding="utf-8")))
        sources.append(local_path)
    runner = data.get("runner", {})
    # Native field-test builds use this explicit application-data boundary.
    # Existing server deployments retain SMARTWATCH_CLANK_DB or the tracked
    # repository-relative default exactly as before when it is unset.
    data_dir = os.environ.get("SMARTWATCH_CLANK_DATA_DIR")
    configured_database = os.environ.get("SMARTWATCH_CLANK_DB")
    if configured_database:
        database = Path(configured_database)
    elif data_dir:
        directory = Path(data_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        database = directory / "smartwatch-clank.sqlite3"
    else:
        database = Path(data.get("database", "var/smartwatch-clank.sqlite3"))
    if not database.is_absolute():
        database = PROJECT_ROOT / database
    scope_path = config_path("scope.yaml")
    scope_data = json.loads(scope_path.read_text(encoding="utf-8")) if scope_path.exists() else {}
    return RuntimeConfig(
        RunnerConfig(
            unexpected_zero_is_failure=runner.get("unexpected_zero_is_failure", True),
            catalogue_warning_ratio=runner.get("catalogue_warning_ratio", 0.75),
            catalogue_failure_ratio=runner.get("catalogue_failure_ratio", 0.50),
        ),
        tuple(data.get("production_allowlist", ())), database, tuple(sources),
        _fingerprint(data, scope_data),
    )
