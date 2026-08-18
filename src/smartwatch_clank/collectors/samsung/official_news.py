from __future__ import annotations

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.news_collector import OfficialNewsCollector

FEED_URL = "https://news.samsung.com/global/feed"


class SamsungOfficialNewsCollector(OfficialNewsCollector):
    def __init__(self, client: HttpClient | None = None) -> None:
        super().__init__(oem="samsung", feed_url=FEED_URL, name="samsung_official_news", client=client)
