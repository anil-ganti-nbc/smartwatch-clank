import unittest

from smartwatch_clank.core.diff import diff_catalogues
from smartwatch_clank.core.models import ChangeType, Confidence, EditorialLevel, SourceClass
from tests.helpers import observation


class DiffNewsTests(unittest.TestCase):
    def test_new_news_identity_is_news_item_appeared_not_new_device(self):
        current = {
            "samsung:news:1": observation(
                "samsung_official_news", "samsung:news:1", source_class=SourceClass.OFFICIAL_NEWS.value,
                classification_state="SMARTWATCH_RELEVANT",
            ),
        }
        discoveries = diff_catalogues({}, current)
        self.assertEqual(len(discoveries), 1)
        self.assertEqual(discoveries[0].change_type, ChangeType.NEWS_ITEM_APPEARED)
        self.assertEqual(discoveries[0].confidence, Confidence.HIGH)
        self.assertEqual(discoveries[0].editorial_level, EditorialLevel.NEWSWORTHY)

    def test_possibly_relevant_news_gets_monitor_level_medium_confidence(self):
        current = {
            "samsung:news:2": observation(
                "samsung_official_news", "samsung:news:2", source_class=SourceClass.OFFICIAL_NEWS.value,
                classification_state="POSSIBLY_SMARTWATCH_RELEVANT",
            ),
        }
        discoveries = diff_catalogues({}, current)
        self.assertEqual(discoveries[0].confidence, Confidence.MEDIUM)
        self.assertEqual(discoveries[0].editorial_level, EditorialLevel.MONITOR)

    def test_not_relevant_news_gets_noise_level_low_confidence(self):
        current = {
            "samsung:news:3": observation(
                "samsung_official_news", "samsung:news:3", source_class=SourceClass.OFFICIAL_NEWS.value,
                classification_state="NOT_SMARTWATCH_RELEVANT",
            ),
        }
        discoveries = diff_catalogues({}, current)
        self.assertEqual(discoveries[0].confidence, Confidence.LOW)
        self.assertEqual(discoveries[0].editorial_level, EditorialLevel.NOISE)

    def test_news_item_rolling_off_the_feed_window_produces_no_discovery(self):
        previous = {
            "samsung:news:1": observation("samsung_official_news", "samsung:news:1", source_class=SourceClass.OFFICIAL_NEWS.value),
        }
        discoveries = diff_catalogues(previous, {})
        self.assertEqual(discoveries, [])

    def test_catalogue_removal_behavior_is_unchanged_for_non_news_source_class(self):
        previous = {
            "dummy:product:1": observation("dummy", "dummy:product:1", source_kind="product_catalogue"),
        }
        discoveries = diff_catalogues(previous, {})
        self.assertEqual(len(discoveries), 1)
        self.assertEqual(discoveries[0].change_type, ChangeType.SOURCE_LISTING_REMOVED)


if __name__ == "__main__":
    unittest.main()
