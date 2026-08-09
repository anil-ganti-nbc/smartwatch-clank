from __future__ import annotations

from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation


class DummyCollector(Collector):
    def __init__(self, name: str = "dummy", items: tuple[Observation, ...] = (), *,
                 tier: CollectorTier = CollectorTier.EXPERIMENTAL, error: Exception | None = None,
                 unexpected_zero_is_failure: bool = True) -> None:
        self.name = name
        self.items = items
        self.tier = tier
        self.error = error
        self.unexpected_zero_is_failure = unexpected_zero_is_failure

    def collect(self, context: CollectionContext) -> CollectorResult:
        if self.error:
            raise self.error
        return CollectorResult(self.items)


def observation(collector: str, identity: str, **changes) -> Observation:
    values = {"collector": collector, "identity": identity,
              "source_url": f"https://official.example/{identity}", "title": identity}
    values.update(changes)
    return Observation(**values)

