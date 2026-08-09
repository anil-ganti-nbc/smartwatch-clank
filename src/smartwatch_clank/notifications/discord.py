from __future__ import annotations

from typing import Protocol

from smartwatch_clank.core.models import Discovery


class DiscoveryNotifier(Protocol):
    def notify(self, discovery: Discovery) -> None: ...


class DiscordNotifier:
    """Stage 1 boundary only; transport will be implemented after collection is proven."""

    def notify(self, discovery: Discovery) -> None:
        raise NotImplementedError("Discord delivery is outside Stage 1 scope")

