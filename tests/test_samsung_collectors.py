from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from smartwatch_clank.collectors.samsung.common import SamsungRegion
from smartwatch_clank.collectors.samsung.product_catalogue import SamsungProductCatalogueCollector
from smartwatch_clank.collectors.samsung.support import SamsungSupportCollector
from smartwatch_clank.core.models import RunScope
from smartwatch_clank.core.registry import CollectorRegistry
from smartwatch_clank.core.runner import Runner
from smartwatch_clank.core.store import SQLiteStore


FIXTURES = Path(__file__).parent / "fixtures" / "samsung"
INDIA = SamsungRegion("IN", "in", "India")
UK = SamsungRegion("GB", "uk", "United Kingdom")
GERMANY = SamsungRegion("DE", "de", "Germany")


class FixtureClient:
    def __init__(self, responses: dict[str, str], error_urls: set[str] | None = None) -> None:
        self.responses = responses
        self.error_urls = error_urls or set()

    def get_text(self, url: str) -> str:
        if url in self.error_urls:
            raise OSError("fixture network failure")
        return self.responses[url]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class SamsungProductTests(unittest.TestCase):
    def collector(self, page: str | None = None) -> SamsungProductCatalogueCollector:
        return SamsungProductCatalogueCollector(
            FixtureClient({INDIA.catalogue_url: page or fixture("product_catalogue.html")}), (INDIA,)
        )

    def test_classification_identity_dimensions_region_and_duplicates(self):
        result = self.collector().run()
        by_model = {item.regional_model_number: item for item in result.observations}
        self.assertEqual(len(result.observations), 4)
        watch = by_model["SM-L340NZKAINS"]
        self.assertEqual(watch.classification_state, "known_smartwatch")
        self.assertEqual(watch.model_number, "SM-L340")
        self.assertEqual(watch.product_family, "Galaxy Watch9")
        self.assertEqual(watch.region, "IN")
        self.assertEqual(watch.size, "4.0 cm")
        self.assertEqual(watch.connectivity, "Bluetooth/Wi-Fi")
        self.assertEqual(watch.colour, "Graphite")
        lte = by_model["SM-L355FZSAINS"]
        self.assertEqual((lte.size, lte.connectivity, lte.colour), ("44 mm", "LTE", "Silver"))
        self.assertEqual(by_model["SM-L800FZBAINS"].product_family, "Galaxy Watch Nova")
        unknown_family = next(item for item in result.observations if item.product_name == "Samsung Wrist Computer One")
        self.assertEqual(unknown_family.classification_state, "probable_smartwatch")
        rejected_names = {item["name"] for item in result.metadata["rejected"]}
        self.assertEqual(rejected_names, {"Galaxy Fit3", "Galaxy S26"})

    def test_baseline_addition_zero_collapse_and_isolation_use_stage1_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "samsung.sqlite3")
            try:
                registry = CollectorRegistry()
                product = self.collector()
                registry.register(product)
                first = Runner(registry, store).run(RunScope.ALL)[0]
                self.assertTrue(first.baseline)
                self.assertEqual(first.discovery_count, 0)
                changed = fixture("product_catalogue.html").replace(
                    "  ]\n}", '    ,{"@type":"ListItem","item":{"@type":"Product","url":"https://www.samsung.com/in/watches/galaxy-watch/galaxy-watch-new-sm-l900nzkains/","name":"Galaxy Watch New"}}\n  ]\n}'
                )
                product.client.responses[INDIA.catalogue_url] = changed
                second = Runner(registry, store).run(RunScope.ALL)[0]
                self.assertEqual(second.discovery_count, 1)
                product.client.responses[INDIA.catalogue_url] = "<html></html>"
                failed = Runner(registry, store).run(RunScope.ALL)[0]
                self.assertFalse(failed.healthy)
                self.assertEqual(store.counts()["discoveries"], 1)
            finally:
                store.close()

    def test_review_count_churn_is_not_normalized_as_a_product_change(self):
        first_page = fixture("product_catalogue.html").replace(
            '"name":"Galaxy Watch9 (Bluetooth, 4.0 cm)",',
            '"name":"Galaxy Watch9 (Bluetooth, 4.0 cm)","aggregateRating":{"ratingCount":10},'
        )
        second_page = first_page.replace('"ratingCount":10', '"ratingCount":11')
        first = self.collector(first_page).run().observations
        second = self.collector(second_page).run().observations
        self.assertEqual([item.comparable() for item in first], [item.comparable() for item in second])


class SamsungSupportTests(unittest.TestCase):
    def responses(self) -> dict[str, str]:
        return {
            INDIA.support_sitemap_url: fixture("support_sitemap.xml"),
            "https://www.samsung.com/in/support/model/SM-L340NZKAINS/": fixture("support_watch.html"),
            "https://www.samsung.com/in/support/model/SM-L999ZZZAINU/": fixture("support_ambiguous.html"),
            "https://www.samsung.com/in/support/model/SM-L100FITAINU/": fixture("support_fit.html"),
        }

    def test_support_observation_and_unknown_model_are_retained(self):
        result = SamsungSupportCollector(INDIA, FixtureClient(self.responses()), max_workers=1).run()
        by_model = {item.regional_model_number: item for item in result.observations}
        self.assertEqual(by_model["SM-L340NZKAINS"].classification_state, "known_smartwatch")
        self.assertEqual(by_model["SM-L340NZKAINS"].source_kind, "support")
        self.assertEqual(by_model["SM-L999ZZZAINU"].classification_state, "ambiguous")
        self.assertEqual(by_model["SM-L999ZZZAINU"].product_name, None)
        self.assertEqual(by_model["SM-L100FITAINU"].classification_state, "non_smartwatch")

    def test_support_page_failure_raises_for_runner_isolation(self):
        failed_url = "https://www.samsung.com/in/support/model/SM-L999ZZZAINU/"
        collector = SamsungSupportCollector(INDIA, FixtureClient(self.responses(), {failed_url}), max_workers=1)
        with self.assertRaises(RuntimeError):
            collector.run()

    def regional_responses(self, region: SamsungRegion) -> dict[str, str]:
        sitemap = fixture("support_sitemap.xml").replace("/in/", f"/{region.locale_path}/")
        return {
            region.support_sitemap_url: sitemap,
            f"https://www.samsung.com/{region.locale_path}/support/model/SM-L340NZKAINS/": fixture("support_watch.html"),
            f"https://www.samsung.com/{region.locale_path}/support/model/SM-L999ZZZAINU/": fixture("support_ambiguous.html"),
            f"https://www.samsung.com/{region.locale_path}/support/model/SM-L100FITAINU/": fixture("support_fit.html"),
        }

    def test_uk_and_germany_have_independent_baselines(self):
        registry = CollectorRegistry()
        registry.register(SamsungSupportCollector(UK, FixtureClient(self.regional_responses(UK)), max_workers=1))
        registry.register(SamsungSupportCollector(GERMANY, FixtureClient(self.regional_responses(GERMANY)), max_workers=1))
        with tempfile.TemporaryDirectory() as directory, SQLiteStore(Path(directory) / "regional.sqlite3") as store:
            outcomes = Runner(registry, store).run(RunScope.ALL)
            self.assertEqual([(o.collector, o.baseline, o.observation_count) for o in outcomes], [
                ("samsung_support_de", True, 3), ("samsung_support_gb", True, 3)
            ])

    def test_one_support_region_failure_does_not_destroy_another(self):
        registry = CollectorRegistry()
        registry.register(SamsungSupportCollector(INDIA, FixtureClient(self.responses()), max_workers=1))
        broken = FixtureClient(self.regional_responses(UK), {UK.support_sitemap_url})
        registry.register(SamsungSupportCollector(UK, broken, max_workers=1))
        with tempfile.TemporaryDirectory() as directory, SQLiteStore(Path(directory) / "isolated.sqlite3") as store:
            outcomes = Runner(registry, store).run(RunScope.ALL)
            self.assertEqual([(o.collector, o.healthy) for o in outcomes], [
                ("samsung_support_gb", False), ("samsung_support_in", True)
            ])
            self.assertEqual(len(store.last_healthy_catalogue("samsung_support_in")), 3)

    def test_support_collapse_keeps_last_healthy_snapshot(self):
        responses = self.responses()
        collector = SamsungSupportCollector(INDIA, FixtureClient(responses), max_workers=1)
        registry = CollectorRegistry()
        registry.register(collector)
        with tempfile.TemporaryDirectory() as directory, SQLiteStore(Path(directory) / "collapse.sqlite3") as store:
            Runner(registry, store).run(RunScope.ALL)
            responses[INDIA.support_sitemap_url] = (
                "<urlset><url><loc>https://www.samsung.com/in/support/model/SM-L340NZKAINS/</loc></url></urlset>"
            )
            outcome = Runner(registry, store).run(RunScope.ALL)[0]
            self.assertFalse(outcome.healthy)
            self.assertEqual(len(store.last_healthy_catalogue("samsung_support_in")), 3)


if __name__ == "__main__":
    unittest.main()
