from __future__ import annotations

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.news_collector import OfficialNewsCollector

FEED_URL = "https://us.amazfit.com/blogs/news.atom"


class AmazfitOfficialNewsCollector(OfficialNewsCollector):
    def __init__(self, client: HttpClient | None = None) -> None:
        super().__init__(oem="amazfit", feed_url=FEED_URL, name="amazfit_official_news", client=client)
