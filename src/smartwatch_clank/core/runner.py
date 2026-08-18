from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass

from .diff import diff_catalogues
from .health import CatalogueHealthError, assess_catalogue
from .models import CollectorTier, HealthRecord, RunOutcome, utc_now
from .registry import CollectorRegistry
from .store import SQLiteStore


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    unexpected_zero_is_failure: bool = True
    catalogue_warning_ratio: float = 0.75
    catalogue_failure_ratio: float = 0.50

    def __post_init__(self) -> None:
        if not 0 <= self.catalogue_failure_ratio <= self.catalogue_warning_ratio <= 1:
            raise ValueError("catalogue ratios must satisfy 0 <= failure <= warning <= 1")


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """Portable identity of the code/config that produced a run.

    Persisted per run so a discovery can be traced back to exactly which
    collector implementation, config, and schema version produced it, and so
    a database can move between hosts without losing that trail.
    """

    app_version: str | None = None
    config_fingerprint: str | None = None
    git_revision: str | None = None


class Runner:
    def __init__(self, registry: CollectorRegistry, store: SQLiteStore, config: RunnerConfig | None = None,
                 provenance: RunProvenance | None = None) -> None:
        self.registry = registry
        self.store = store
        self.config = config or RunnerConfig()
        self.provenance = provenance or RunProvenance()

    def run(self, mode: CollectorTier, production_allowlist: tuple[str, ...] = (),
            run_metadata: dict | None = None) -> list[RunOutcome]:
        return [self._run_one(collector, run_metadata or {})
                for collector in self.registry.selected(mode, production_allowlist)]

    def run_selected(self, names: tuple[str, ...], production_allowlist: tuple[str, ...],
                     run_metadata: dict | None = None) -> list[RunOutcome]:
        """Run explicit production sources now; scheduling policy remains external."""
        allowed = set(production_allowlist)
        collectors = []
        for name in names:
            collector = self.registry.get(name)
            if collector.tier is not CollectorTier.PRODUCTION or name not in allowed:
                raise ValueError(f"collector is not production-enabled: {name}")
            collectors.append(collector)
        return [self._run_one(collector, run_metadata or {}) for collector in collectors]

    def _run_one(self, collector, run_metadata: dict) -> RunOutcome:
        started = utc_now()
        run_uuid = str(uuid.uuid4())
        attempted_count = 0
        previous = self.store.last_healthy_catalogue(collector.name)
        previous_count = len(previous) if self.store.has_healthy_run(collector.name) else None
        try:
            result = collector.run()
            attempted_count = len(result.observations)
            identities = [item.identity for item in result.observations]
            if any(item.collector != collector.name for item in result.observations):
                raise ValueError("collector emitted an observation under a different collector name")
            if len(identities) != len(set(identities)):
                raise ValueError("collector emitted duplicate observation identities")
            assessment = assess_catalogue(
                len(result.observations), previous_count,
                unexpected_zero_is_failure=collector.unexpected_zero_is_failure and self.config.unexpected_zero_is_failure,
                warning_ratio=self.config.catalogue_warning_ratio,
                failure_ratio=self.config.catalogue_failure_ratio,
            )
            baseline = previous_count is None
            discoveries = [] if baseline else diff_catalogues(previous, {item.identity: item for item in result.observations})
            finished = utc_now()
            metadata = {**result.metadata}
            if run_metadata:
                metadata["soak"] = run_metadata
            self.store.save_run(collector=collector.name, started_at=started, finished_at=finished, healthy=True,
                                observations=result.observations, discoveries=discoveries,
                                warning=assessment.warning, error=None, metadata=metadata,
                                run_uuid=run_uuid, app_version=self.provenance.app_version,
                                schema_version_at_run=self.store.SCHEMA_VERSION,
                                config_fingerprint=self.provenance.config_fingerprint,
                                git_revision=self.provenance.git_revision)
            self.store.save_health(HealthRecord(collector.name, True, len(result.observations), previous_count,
                                                warning=assessment.warning, checked_at=finished))
            return RunOutcome(collector.name, True, baseline, len(result.observations), len(discoveries), assessment.warning)
        except Exception as exc:
            finished = utc_now()
            detail = f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, (CatalogueHealthError, ValueError)):
                detail += "\n" + "".join(traceback.format_exception(exc)).strip()
            metadata = {"attempted_observation_count": attempted_count}
            if run_metadata:
                metadata["soak"] = run_metadata
            self.store.save_run(collector=collector.name, started_at=started, finished_at=finished, healthy=False,
                                observations=(), discoveries=[], warning=None, error=detail,
                                metadata=metadata, run_uuid=run_uuid, app_version=self.provenance.app_version,
                                schema_version_at_run=self.store.SCHEMA_VERSION,
                                config_fingerprint=self.provenance.config_fingerprint,
                                git_revision=self.provenance.git_revision)
            self.store.save_health(HealthRecord(collector.name, False, attempted_count, previous_count,
                                                error=detail, checked_at=finished))
            return RunOutcome(collector.name, False, False, attempted_count, 0, error=detail)
