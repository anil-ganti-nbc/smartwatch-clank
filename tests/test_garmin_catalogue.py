from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from smartwatch_clank.collectors.garmin.catalogue import (
    GarminCatalogueCollector,
    SITEMAP_URL,
    classify_product,
    parse_sitemap_product_ids,
)
from smartwatch_clank.core.health import SourceHostBlockedError


def ld_json_page(name: str, description: str, url: str, price: str = "849.99") -> str:
    product = {
        "@context": "http://schema.org", "@type": "Product", "name": name, "description": description,
        "url": url, "brand": {"@type": "Brand", "name": "Garmin"},
        "offers": {"@type": "Offer", "price": price, "priceCurrency": "USD"},
    }
    return (
        "<html><head>"
        '<script id="seo-page-schema-data" type="application/ld+json">{"@type":"WebPage"}</script>'
        f'<script id="seo-product-schema-data" type="application/ld+json">{json.dumps(product)}</script>'
        "</head><body></body></html>"
    )


def sitemap(ids: list[str]) -> str:
    locs = "".join(f"<url><loc>https://www.garmin.com/en-US/p/{i}/</loc></url>" for i in ids)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'


class FixtureClient:
    def __init__(self, responses: dict[str, str], error_urls: set[str] | None = None) -> None:
        self.responses = responses
        self.error_urls = error_urls or set()

    def get_text(self, url: str) -> str:
        if url in self.error_urls:
            raise SourceHostBlockedError(f"HTTP 403 fetching {url}")
        return self.responses[url]


FENIX8_URL = "https://www.garmin.com/en-US/p/1228493/"
POLE_MOUNT_URL = "https://www.garmin.com/en-US/p/685409/"
MARQ_URL = "https://www.garmin.com/en-US/p/900001/"
APPROACH_WATCH_URL = "https://www.garmin.com/en-US/p/900002/"
APPROACH_HANDHELD_URL = "https://www.garmin.com/en-US/p/900003/"
UNKNOWN_FAMILY_URL = "https://www.garmin.com/en-US/p/900004/"
NO_SIGNAL_URL = "https://www.garmin.com/en-US/p/900006/"


def base_responses() -> dict[str, str]:
    return {
        SITEMAP_URL: sitemap(["1228493", "685409", "900001", "900002", "900003", "900004", "900006"]),
        FENIX8_URL: ld_json_page(
            "fēnix® 8 – 43 mm, AMOLED", "Sapphire, Soft Gold with Fog Gray/Dark Sandstone Silicone Band", FENIX8_URL,
        ),
        POLE_MOUNT_URL: ld_json_page("0 Degree Pole Mount", "Accessory mount for handlebars", POLE_MOUNT_URL, "19.99"),
        MARQ_URL: ld_json_page("MARQ Adventurer (Gen 2)", "Titanium case with titanium bracelet", MARQ_URL, "1899.99"),
        APPROACH_WATCH_URL: ld_json_page("Approach S12", "Simple, easy-to-use golf GPS watch", APPROACH_WATCH_URL, "199.99"),
        APPROACH_HANDHELD_URL: ld_json_page("Approach G12", "Golf handheld GPS", APPROACH_HANDHELD_URL, "129.99"),
        UNKNOWN_FAMILY_URL: ld_json_page("Zenith X1 Smartwatch", "A brand new kind of wearable", UNKNOWN_FAMILY_URL, "399.99"),
        NO_SIGNAL_URL: ld_json_page("South America Coastal Charts", "Marine chart data", NO_SIGNAL_URL, "49.99"),
    }


class ClassifyProductTests(unittest.TestCase):
    def test_known_family_is_known_smartwatch(self):
        state, evidence = classify_product("fēnix® 8 – 43 mm, AMOLED", "Sapphire")
        self.assertEqual(state, "known_smartwatch")
        self.assertTrue(evidence)

    def test_premium_marq_is_known_smartwatch_not_deprioritized(self):
        state, _ = classify_product("MARQ Adventurer (Gen 2)", "Titanium case")
        self.assertEqual(state, "known_smartwatch")

    def test_accessory_is_rejected(self):
        state, _ = classify_product("0 Degree Pole Mount", "Accessory mount for handlebars")
        self.assertEqual(state, "non_smartwatch")

    def test_approach_s_series_is_known_smartwatch(self):
        state, _ = classify_product("Approach S12", "golf GPS watch")
        self.assertEqual(state, "known_smartwatch")

    def test_approach_handheld_is_rejected(self):
        state, _ = classify_product("Approach G12", "Golf handheld GPS")
        self.assertEqual(state, "non_smartwatch")

    def test_approach_with_real_trademark_symbol_still_classifies_correctly(self):
        # Regression: a live baseline run showed Garmin's real product names
        # carry a registered-trademark symbol directly after the family name
        # ("Approach® S70") -- `\s*` in the Approach sub-patterns doesn't
        # match "®", so every real Approach product (watch AND non-watch)
        # was falling through to "ambiguous" instead of being classified.
        watch_state, _ = classify_product("Approach® S70 - 42 mm", "")
        self.assertEqual(watch_state, "known_smartwatch")
        handheld_state, _ = classify_product("Approach® G80", "")
        self.assertEqual(handheld_state, "non_smartwatch")

    def test_approach_watch_band_accessory_is_rejected_not_matched_as_the_watch(self):
        # Regression: "QuickFit 22 Watch Bands (Approach® S60)" matched the
        # Approach S-series pattern and was misclassified as the watch
        # itself before accessory keywords were checked first.
        state, _ = classify_product("QuickFit® 22 Watch Bands (Approach® S60)", "")
        self.assertEqual(state, "non_smartwatch")

    def test_unknown_family_with_weak_watch_signal_is_probable_not_dropped(self):
        state, _ = classify_product("Zenith X1 Smartwatch", "A brand new kind of wearable")
        self.assertEqual(state, "probable_smartwatch")

    def test_unrelated_non_wearable_product_with_no_watch_signal_is_rejected(self):
        # Regression: a live baseline run against Garmin's real ~4,300-URL
        # product sitemap showed the old "no signal -> ambiguous, retained"
        # fallback produced over 2,000 observations for products with zero
        # watch-adjacent signal at all (nautical charts, GPS antennas,
        # business plans, eLearning courses) -- Garmin's sitemap spans its
        # entire business, not just watches/accessories, so an unmatched
        # item is overwhelmingly more likely to be unrelated than a new
        # watch family. New families still surface via the known-family or
        # weak "watch"/"smartwatch" signal paths above.
        state, _ = classify_product("South America Coastal Charts", "Marine chart data")
        self.assertEqual(state, "non_smartwatch")


class SitemapParsingTests(unittest.TestCase):
    def test_extracts_unique_sorted_ids(self):
        ids = parse_sitemap_product_ids(sitemap(["500", "100", "500"]))
        self.assertEqual(ids, ("100", "500"))


class GarminCatalogueCollectorTests(unittest.TestCase):
    def collector(self, responses=None, cache_path=None, error_urls=None) -> GarminCatalogueCollector:
        return GarminCatalogueCollector(
            FixtureClient(responses or base_responses(), error_urls), cache_path=cache_path, max_workers=2,
        )

    def test_accepts_watches_rejects_accessories_and_unrelated_products(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache.json"
            result = self.collector(cache_path=cache_path).run()
            by_id = {item.identity: item for item in result.observations}
            self.assertIn("garmin:catalogue:1228493", by_id)
            self.assertEqual(by_id["garmin:catalogue:1228493"].classification_state, "known_smartwatch")
            self.assertIn("garmin:catalogue:900001", by_id)  # MARQ, premium, not deprioritized
            self.assertIn("garmin:catalogue:900002", by_id)  # Approach S12
            self.assertIn("garmin:catalogue:900004", by_id)  # unrecognised family, weak "watch" signal, retained
            self.assertEqual(by_id["garmin:catalogue:900004"].classification_state, "probable_smartwatch")
            self.assertNotIn("garmin:catalogue:685409", by_id)  # pole mount, rejected
            self.assertNotIn("garmin:catalogue:900003", by_id)  # Approach handheld, rejected
            self.assertNotIn("garmin:catalogue:900006", by_id)  # coastal charts, no watch signal, rejected
            self.assertEqual(result.metadata["rejected"], 3)
            self.assertEqual(result.metadata["accepted"], 4)

    def test_variant_dimensions_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.collector(cache_path=Path(directory) / "cache.json").run()
            fenix = next(item for item in result.observations if item.identity == "garmin:catalogue:1228493")
            self.assertEqual(fenix.size, "43 mm")
            self.assertEqual(fenix.price, "849.99")
            self.assertEqual(fenix.currency, "USD")

    def test_classified_ids_are_cached_and_not_refetched(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache.json"
            first_client = FixtureClient(base_responses())
            GarminCatalogueCollector(first_client, cache_path=cache_path, max_workers=2).run()
            cached = json.loads(cache_path.read_text())
            self.assertEqual(
                set(cached.keys()), {"1228493", "685409", "900001", "900002", "900003", "900004", "900006"},
            )

            # Second run: only the sitemap and product pages that fail (or are
            # new) need to be fetchable -- everything else was cached.
            second_responses = {SITEMAP_URL: base_responses()[SITEMAP_URL]}
            second_client = FixtureClient(second_responses)
            result = GarminCatalogueCollector(second_client, cache_path=cache_path, max_workers=2).run()
            self.assertEqual(result.metadata["newly_fetched"], 0)
            self.assertEqual(result.metadata["cached"], 7)
            self.assertEqual(result.metadata["accepted"], 4)

    def test_host_blocked_fetch_error_is_not_cached_and_retried_next_run(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache.json"
            responses = base_responses()
            collector = self.collector(responses, cache_path=cache_path, error_urls={FENIX8_URL})
            result = collector.run()
            self.assertEqual(result.metadata["fetch_errors"], 1)
            cached = json.loads(cache_path.read_text())
            self.assertNotIn("1228493", cached)

            # Retry without the block: should be fetched again, not skipped.
            retry = GarminCatalogueCollector(FixtureClient(responses), cache_path=cache_path, max_workers=2).run()
            self.assertEqual(retry.metadata["fetch_errors"], 0)
            self.assertEqual(retry.metadata["newly_fetched"], 1)

    def test_non_typed_fetch_failure_is_isolated_not_fatal(self):
        # Regression: a real live run hit an HTTP 404 (a stale sitemap entry
        # for a removed product) and it propagated as a raw HTTPError,
        # crashing the whole 4,300-page crawl instead of being treated as
        # one failed item among many. `_fetch_one` must catch everything,
        # not just the typed SourceHealthError subclasses.
        class FlakyClient:
            def __init__(self, responses: dict[str, str]) -> None:
                self.responses = responses

            def get_text(self, url: str) -> str:
                if url not in self.responses:
                    raise ValueError(f"HTTP 404: {url}")
                return self.responses[url]

        with tempfile.TemporaryDirectory() as directory:
            responses = base_responses()
            del responses[MARQ_URL]  # simulate a stale sitemap entry -> 404
            collector = GarminCatalogueCollector(
                FlakyClient(responses), cache_path=Path(directory) / "cache.json", max_workers=2,
            )
            result = collector.run()  # must not raise
            self.assertEqual(result.metadata["fetch_errors"], 1)
            identities = {item.identity for item in result.observations}
            self.assertNotIn("garmin:catalogue:900001", identities)
            self.assertIn("garmin:catalogue:1228493", identities)  # other products still processed

    def test_baseline_then_new_watch_is_a_discovery_via_runner(self):
        from smartwatch_clank.core.models import RunScope
        from smartwatch_clank.core.registry import CollectorRegistry
        from smartwatch_clank.core.runner import Runner
        from smartwatch_clank.core.store import SQLiteStore

        with tempfile.TemporaryDirectory() as directory:
            responses = base_responses()
            cache_path = Path(directory) / "cache.json"
            client = FixtureClient(responses)
            collector = GarminCatalogueCollector(client, cache_path=cache_path, max_workers=2)
            registry = CollectorRegistry()
            registry.register(collector)
            store = SQLiteStore(Path(directory) / "garmin.sqlite3")
            try:
                first = Runner(registry, store).run(RunScope.ALL)[0]
                self.assertTrue(first.baseline)
                self.assertEqual(first.discovery_count, 0)

                new_url = "https://www.garmin.com/en-US/p/900005/"
                responses[SITEMAP_URL] = sitemap(
                    ["1228493", "685409", "900001", "900002", "900003", "900004", "900005"]
                )
                responses[new_url] = ld_json_page("Instinct 3", "Rugged solar GPS watch", new_url, "449.99")
                second = Runner(registry, store).run(RunScope.ALL)[0]
                self.assertEqual(second.discovery_count, 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
