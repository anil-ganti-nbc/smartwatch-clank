from __future__ import annotations

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.news_collector import OfficialNewsCollector
from smartwatch_clank.core.models import CollectorTier

FEED_URL = "https://www.garmin.com/en-US/newsroom/feed/"


class GarminOfficialNewsCollector(OfficialNewsCollector):
    # Promoted to PRODUCTION maturity 2026-09-05 by explicit operator
    # decision (the soak/promotion queue was overridden, not re-run).
    # Health and error history are unchanged and still reported
    # independently of maturity.
    tier = CollectorTier.PRODUCTION

    def __init__(self, client: HttpClient | None = None) -> None:
        super().__init__(oem="garmin", feed_url=FEED_URL, name="garmin_official_news", client=client)
