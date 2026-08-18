from __future__ import annotations

import json
import unittest
from pathlib import Path

from smartwatch_clank.collectors.amazfit.catalogue import AmazfitCatalogueCollector, PRODUCTS_URL, classify_product

FIXTURES = Path(__file__).parent / "fixtures" / "amazfit"


class FixtureClient:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses

    def get_json(self, url: str):
        return self.responses[url]


def fixture_json() -> dict:
    return json.loads((FIXTURES / "products.json").read_text(encoding="utf-8"))


class ClassifyProductTests(unittest.TestCase):
    def test_known_family_is_known_smartwatch(self):
        state, _ = classify_product("Balance Ultra", "Smartwatch", ())
        self.assertEqual(state, "known_smartwatch")

    def test_unlisted_family_matched_by_name_is_still_known(self):
        state, _ = classify_product("Falcon Ultra", "", ())
        self.assertEqual(state, "known_smartwatch")

    def test_helio_strap_is_rejected_despite_smartwatch_product_type(self):
        state, _ = classify_product("Helio Strap Pro", "Smartwatch", ())
        self.assertEqual(state, "non_smartwatch")

    def test_accessories_product_type_is_rejected(self):
        state, _ = classify_product("Sport Silicone Straps (20/22mm)", "Accessories", ())
        self.assertEqual(state, "non_smartwatch")

    def test_bundle_listing_is_rejected(self):
        state, _ = classify_product("Amazfit T-Rex 3 + Helio Strap", "", ())
        self.assertEqual(state, "non_smartwatch")

    def test_gift_card_is_rejected(self):
        state, _ = classify_product("Amazfit Gift Card", "", ())
        self.assertEqual(state, "non_smartwatch")

    def test_trade_in_listing_is_rejected(self):
        state, _ = classify_product("T-Rex 3 Trade-In", "", ())
        self.assertEqual(state, "non_smartwatch")

    def test_unknown_family_with_no_signal_is_ambiguous_not_dropped(self):
        state, _ = classify_product("Zenith Nova", "", ())
        self.assertEqual(state, "ambiguous")


class AmazfitCatalogueCollectorTests(unittest.TestCase):
    def collector(self) -> AmazfitCatalogueCollector:
        return AmazfitCatalogueCollector(FixtureClient({PRODUCTS_URL: fixture_json()}))

    def test_accepts_watches_rejects_accessories_bundles_and_giftcards(self):
        result = self.collector().run()
        by_id = {item.identity: item for item in result.observations}
        self.assertIn("amazfit:catalogue:9111111111111", by_id)  # Balance Ultra
        self.assertIn("amazfit:catalogue:9111111111112", by_id)  # T-Rex 3 Pro
        self.assertIn("amazfit:catalogue:9111111111119", by_id)  # Falcon Ultra, unlisted-but-matched family
        self.assertIn("amazfit:catalogue:9111111111120", by_id)  # Zenith Nova, ambiguous, still retained
        self.assertEqual(by_id["amazfit:catalogue:9111111111120"].classification_state, "ambiguous")
        for rejected_id in (9111111111113, 9111111111114, 9111111111115, 9111111111116, 9111111111117, 9111111111118):
            self.assertNotIn(f"amazfit:catalogue:{rejected_id}", by_id)
        self.assertEqual(result.metadata["accepted"], 4)
        self.assertEqual(result.metadata["rejected"], 6)

    def test_variant_dimensions_extracted(self):
        result = self.collector().run()
        balance = next(item for item in result.observations if item.identity == "amazfit:catalogue:9111111111111")
        self.assertEqual(balance.colour, "Titanium")
        self.assertEqual(balance.size, "46mm")
        self.assertEqual(balance.sku, "W2553GL2N")
        self.assertEqual(balance.price, "599.99")

    def test_identities_stable_across_runs(self):
        collector = self.collector()
        first = {item.identity for item in collector.run().observations}
        second = {item.identity for item in collector.run().observations}
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
