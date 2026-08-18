from __future__ import annotations

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.news_collector import OfficialNewsCollector

FEED_URL = "https://www.garmin.com/en-US/newsroom/feed/"


class GarminOfficialNewsCollector(OfficialNewsCollector):
    def __init__(self, client: HttpClient | None = None) -> None:
        super().__init__(oem="garmin", feed_url=FEED_URL, name="garmin_official_news", client=client)
