from __future__ import annotations

from collections.abc import Iterable

from .collector import Collector
from .models import CollectorTier


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

    def selected(self, mode: CollectorTier, production_allowlist: Iterable[str] = ()) -> tuple[Collector, ...]:
        if mode is CollectorTier.EXPERIMENTAL:
            return self.all()
        allowed = set(production_allowlist)
        return tuple(c for c in self.all() if c.tier is CollectorTier.PRODUCTION and c.name in allowed)

