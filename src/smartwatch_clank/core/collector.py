from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from .models import CollectorResult, CollectorTier, utc_now


@dataclass(frozen=True, slots=True)
class CollectionContext:
    started_at: datetime


class Collector(ABC):
    name: str
    tier: CollectorTier = CollectorTier.EXPERIMENTAL
    unexpected_zero_is_failure: bool = True

    @abstractmethod
    def collect(self, context: CollectionContext) -> CollectorResult:
        """Return a complete normalized catalogue snapshot."""

    def run(self) -> CollectorResult:
        return self.collect(CollectionContext(started_at=utc_now()))

