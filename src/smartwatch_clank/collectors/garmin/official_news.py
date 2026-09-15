from __future__ import annotations

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.news_collector import OfficialNewsCollector
from smartwatch_clank.core.models import CollectorTier

FEED_URL = "https://www.garmin.com/en-US/newsroom/feed/"


class GarminOfficialNewsCollector(OfficialNewsCollector):
    # Policy alignment 2026-09-15: production qualification is incomplete.
    # Preserve the collector and its evidence while a safe Garmin relay path
    # and a fresh bounded soak remain unproven.
    tier = CollectorTier.EXPERIMENTAL

    def __init__(self, client: HttpClient | None = None) -> None:
        super().__init__(oem="garmin", feed_url=FEED_URL, name="garmin_official_news", client=client)
