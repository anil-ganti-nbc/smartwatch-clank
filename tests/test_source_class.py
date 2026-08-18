import unittest

from smartwatch_clank.core.models import Observation, SourceClass
from tests.helpers import observation


class SourceClassTests(unittest.TestCase):
    def test_existing_source_kind_strings_still_match(self):
        self.assertEqual(SourceClass.PRODUCT_CATALOGUE, "product_catalogue")
        self.assertEqual(SourceClass.SUPPORT, "support")

    def test_new_values_are_distinct(self):
        values = {member.value for member in SourceClass}
        self.assertEqual(len(values), len(list(SourceClass)))
        self.assertIn("official_news", values)
        self.assertIn("software_update", values)
        self.assertIn("companion_app", values)
        self.assertIn("certification", values)

    def test_observation_carries_source_class_and_oem_through_comparable(self):
        item = observation("dummy", "watch-1", source_class=SourceClass.OFFICIAL_NEWS.value, oem="samsung")
        self.assertEqual(item.source_class, "official_news")
        self.assertEqual(item.oem, "samsung")
        self.assertEqual(item.comparable()["source_class"], "official_news")

    def test_source_class_field_defaults_to_none(self):
        item = observation("dummy", "watch-1")
        self.assertIsNone(item.source_class)
        self.assertIsNone(item.oem)


if __name__ == "__main__":
    unittest.main()
