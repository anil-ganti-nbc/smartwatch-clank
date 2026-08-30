from __future__ import annotations

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.news_collector import OfficialNewsCollector
from smartwatch_clank.core.models import CollectorTier

FEED_URL = "https://us.amazfit.com/blogs/news.atom"


class AmazfitOfficialNewsCollector(OfficialNewsCollector):
    # Promoted to PRODUCTION 2026-08-30 after experimental soak (see config/config.yaml allowlist).
    tier = CollectorTier.PRODUCTION
    def __init__(self, client: HttpClient | None = None) -> None:
        super().__init__(oem="amazfit", feed_url=FEED_URL, name="amazfit_official_news", client=client)
