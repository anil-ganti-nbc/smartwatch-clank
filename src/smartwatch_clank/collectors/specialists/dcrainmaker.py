"""Specialist wearable-press collector (Wave 2, EXPERIMENTAL ONLY,
source_key "dcrainmaker_specialist").

One specialist source, chosen per the Wave 2 brief's cap of 2–3 and the
"demonstrates DISTINCT discovery value" bar:

DC Rainmaker (dcrainmaker.com) is the most established first-to-publish
wearable/sports-tech specialist, with a track record of surfacing
Garmin/COROS/Amazfit firmware notes, certification filings (FCC), and
launch leaks days-to-weeks before OEM newsrooms publish anything. Live
probe 2026-08-28: `https://www.dcrainmaker.com/feed` is a real RSS 2.0
feed. Generic tech publications remain excluded per policy.

Reuse, not redesign: this collector subclasses the existing
`OfficialNewsCollector` (same fetch -> parse -> classify -> Observation
pipeline as the four OEM official-news collectors). The only Wave 2 delta
is a specialist phrase set appended for classification - additive to
`classifiers/news.py`, verified zero effect on existing OEM
classification assertions.

Classification semantics: DC Rainmaker covers non-watch sport-tech too
(bike sensors, running power meters), so single-word "watch" matching is
never sufficient here; the existing cascade (specific phrases -> excluded
phrases -> weak signal) already encodes that discipline.
"""

from __future__ import annotations

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.news_collector import OfficialNewsCollector

FEED_URL = "https://www.dcrainmaker.com/feed"


class DCRainmakerSpecialistCollector(OfficialNewsCollector):
    def __init__(self, client: HttpClient | None = None) -> None:
        super().__init__(
            oem="specialist_dcrainmaker",
            feed_url=FEED_URL,
            name="dcrainmaker_specialist",
            client=client,
        )
