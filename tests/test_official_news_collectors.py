from __future__ import annotations

import unittest
from pathlib import Path

from smartwatch_clank.collectors.amazfit.official_news import AmazfitOfficialNewsCollector, FEED_URL as AMAZFIT_FEED_URL
from smartwatch_clank.collectors.apple.official_news import AppleOfficialNewsCollector, FEED_URL as APPLE_FEED_URL
from smartwatch_clank.collectors.garmin.official_news import FEED_URL as GARMIN_FEED_URL, GarminOfficialNewsCollector
from smartwatch_clank.collectors.google.official_news import FEED_URL as GOOGLE_FEED_URL, GoogleOfficialNewsCollector
from smartwatch_clank.collectors.samsung.official_news import FEED_URL as SAMSUNG_FEED_URL, SamsungOfficialNewsCollector
from smartwatch_clank.core.models import CollectorTier

FIXTURES = Path(__file__).parent / "fixtures" / "news"


class FixtureClient:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses

    def get_text(self, url: str) -> str:
        return self.responses[url]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class OfficialNewsCollectorTests(unittest.TestCase):
    def test_samsung_official_news_classifies_and_reports_counts(self):
        collector = SamsungOfficialNewsCollector(FixtureClient({SAMSUNG_FEED_URL: fixture("samsung.xml")}))
        result = collector.run()
        self.assertEqual(collector.tier, CollectorTier.EXPERIMENTAL)
        self.assertEqual(len(result.observations), 4)
        by_title = {item.title: item for item in result.observations}
        self.assertEqual(
            by_title["Samsung Officially Launches Galaxy Z Fold8 Ultra, Fold8, Flip8, Watch Ultra2 and Watch9"].classification_state,
            "SMARTWATCH_RELEVANT",
        )
        self.assertEqual(by_title["Galaxy Fit3 Brings Simple, Stylish Wellness Tracking to Everyone"].classification_state, "NOT_SMARTWATCH_RELEVANT")
        self.assertEqual(result.metadata["classification_counts"]["SMARTWATCH_RELEVANT"], 1)
        self.assertTrue(all(item.oem == "samsung" and item.source_class == "official_news" for item in result.observations))

    def test_garmin_official_news_excludes_smart_band_and_approach(self):
        collector = GarminOfficialNewsCollector(FixtureClient({GARMIN_FEED_URL: fixture("garmin.xml")}))
        result = collector.run()
        by_title = {item.title: item for item in result.observations}
        self.assertEqual(by_title["Meet CIRQA Smart Band: The screen-free health and fitness tracker from Garmin"].classification_state, "NOT_SMARTWATCH_RELEVANT")
        self.assertEqual(by_title["Take the guesswork out of the game with Approach Z10, a compact laser rangefinder from Garmin"].classification_state, "NOT_SMARTWATCH_RELEVANT")
        self.assertEqual(by_title["Garmin unveils Forerunner 970, its most advanced running watch yet"].classification_state, "SMARTWATCH_RELEVANT")

    def test_google_official_news_classifies_pixel_watch(self):
        collector = GoogleOfficialNewsCollector(FixtureClient({GOOGLE_FEED_URL: fixture("google.xml")}))
        result = collector.run()
        by_title = {item.title: item for item in result.observations}
        self.assertEqual(by_title["Pixel Watch 5: Proactive assistance and advanced health tracking on your wrist"].classification_state, "SMARTWATCH_RELEVANT")
        self.assertEqual(by_title["Tips to watch your Pixel's battery health over time"].classification_state, "POSSIBLY_SMARTWATCH_RELEVANT")

    def test_apple_official_news_classifies_apple_watch(self):
        collector = AppleOfficialNewsCollector(FixtureClient({APPLE_FEED_URL: fixture("apple.xml")}))
        result = collector.run()
        by_title = {item.title: item for item in result.observations}
        self.assertEqual(by_title["Apple introduces Apple Watch Series 12 with new health features"].classification_state, "SMARTWATCH_RELEVANT")
        self.assertEqual(by_title["Apple opens Advanced Manufacturing Center in Houston"].classification_state, "NOT_SMARTWATCH_RELEVANT")

    def test_amazfit_official_news_excludes_helio_strap_and_classifies_launch(self):
        collector = AmazfitOfficialNewsCollector(FixtureClient({AMAZFIT_FEED_URL: fixture("amazfit.xml")}))
        result = collector.run()
        by_title = {item.title: item for item in result.observations}
        self.assertEqual(
            by_title["Amazfit Introduces Active Max: Bigger, Brighter, and Built for Maximum Performance"].classification_state,
            "SMARTWATCH_RELEVANT",
        )
        self.assertEqual(by_title["Introducing the Helio Strap Pro"].classification_state, "NOT_SMARTWATCH_RELEVANT")
        self.assertEqual(
            by_title["Zepp Health's Amazfit Adds Athlete Ambassadors to Team Amazfit Roster"].classification_state,
            "NOT_SMARTWATCH_RELEVANT",
        )
        self.assertTrue(all(item.oem == "amazfit" for item in result.observations))

    def test_identities_are_stable_and_unique_across_runs(self):
        client = FixtureClient({SAMSUNG_FEED_URL: fixture("samsung.xml")})
        collector = SamsungOfficialNewsCollector(client)
        first = {item.identity for item in collector.run().observations}
        second = {item.identity for item in collector.run().observations}
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        self.assertTrue(all(identity.startswith("samsung:news:") for identity in first))


if __name__ == "__main__":
    unittest.main()
