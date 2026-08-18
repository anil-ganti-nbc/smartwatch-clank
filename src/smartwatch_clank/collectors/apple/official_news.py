from __future__ import annotations

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.news_collector import OfficialNewsCollector

FEED_URL = "https://www.apple.com/newsroom/rss-feed.rss"


class AppleOfficialNewsCollector(OfficialNewsCollector):
    def __init__(self, client: HttpClient | None = None) -> None:
        super().__init__(oem="apple", feed_url=FEED_URL, name="apple_official_news", client=client)
