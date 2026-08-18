from __future__ import annotations

from collections.abc import Iterable

from .collector import Collector
from .models import CollectorTier, RunScope


class CollectorRegistry:
    def __init__(self) -> None:
        self._collectors: dict[str, Collector] = {}

    def register(self, collector: Collector) -> None:
        if not collector.name or collector.name in self._collectors:
            raise ValueError(f"Collector name must be unique and non-empty: {collector.name!r}")
        self._collectors[collector.name] = collector

    def get(self, name: str) -> Collector:
        return self._collectors[name]

    def all(self) -> tuple[Collector, ...]:
        return tuple(self._collectors[name] for name in sorted(self._collectors))

    def selected(self, scope: RunScope, production_allowlist: Iterable[str] = ()) -> tuple[Collector, ...]:
        """Select collectors for one run, per `RunScope`.

        EXPERIMENTAL is tier-only and deliberately never consults
        `production_allowlist` -- a future experimental collector joins
        the experimental soak automatically by virtue of its tier, and a
        future production-tier collector never runs anywhere without
        being explicitly allowlisted first.
        """
        if scope is RunScope.ALL:
            return self.all()
        if scope is RunScope.EXPERIMENTAL:
            return tuple(c for c in self.all() if c.tier is CollectorTier.EXPERIMENTAL)
        allowed = set(production_allowlist)
        return tuple(c for c in self.all() if c.tier is CollectorTier.PRODUCTION and c.name in allowed)

