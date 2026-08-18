from __future__ import annotations

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.news_collector import OfficialNewsCollector

# Documented public URL; redirects (301) to
# blog.google/products-and-platforms/devices/pixel/rss/ as of Stage B
# research. urllib.request.urlopen follows redirects by default, so this
# URL is used as-is rather than the resolved target.
FEED_URL = "https://blog.google/products/pixel/rss/"


class GoogleOfficialNewsCollector(OfficialNewsCollector):
    def __init__(self, client: HttpClient | None = None) -> None:
        super().__init__(oem="google", feed_url=FEED_URL, name="google_official_news", client=client)
