import unittest

from smartwatch_clank.classifiers.news import NewsClassification, classify_news


class NewsClassificationTests(unittest.TestCase):
    def test_galaxy_watch_category_is_relevant(self):
        state, evidence = classify_news(
            "samsung", "Samsung Officially Launches Galaxy Z Fold8 Ultra, Fold8, Flip8, Watch Ultra2 and Watch9",
            ("Mobile", "Galaxy Watch9"),
        )
        self.assertEqual(state, NewsClassification.SMARTWATCH_RELEVANT)
        self.assertTrue(any("galaxy watch" in e for e in evidence))

    def test_pixel_watch_title_is_relevant(self):
        state, _ = classify_news("google", "Pixel Watch 5: Proactive assistance on your wrist", ("Google Health", "Pixel"))
        self.assertEqual(state, NewsClassification.SMARTWATCH_RELEVANT)

    def test_apple_watch_title_is_relevant(self):
        state, _ = classify_news("apple", "Apple introduces Apple Watch Series 12 with new health features", ("PRESS RELEASE",))
        self.assertEqual(state, NewsClassification.SMARTWATCH_RELEVANT)

    def test_garmin_forerunner_title_is_relevant(self):
        state, _ = classify_news("garmin", "Garmin unveils Forerunner 970, its most advanced running watch yet", ("Wearables / Health",))
        self.assertEqual(state, NewsClassification.SMARTWATCH_RELEVANT)

    def test_real_garmin_cirqa_smart_band_is_not_relevant(self):
        state, evidence = classify_news(
            "garmin", "Meet CIRQA Smart Band: The screen-free health and fitness tracker from Garmin",
            ("Wearables / Health", "health", "wellness"),
        )
        self.assertEqual(state, NewsClassification.NOT_SMARTWATCH_RELEVANT)
        self.assertTrue(any("smart band" in e for e in evidence))

    def test_galaxy_fit_is_not_relevant(self):
        state, _ = classify_news("samsung", "Galaxy Fit3 Brings Simple, Stylish Wellness Tracking to Everyone", ("Mobile", "Galaxy Fit3"))
        self.assertEqual(state, NewsClassification.NOT_SMARTWATCH_RELEVANT)

    def test_real_garmin_approach_rangefinder_is_not_auto_relevant(self):
        state, _ = classify_news(
            "garmin", "Take the guesswork out of the game with Approach Z10, a compact laser rangefinder from Garmin",
            ("Outdoor", "sports"),
        )
        self.assertEqual(state, NewsClassification.NOT_SMARTWATCH_RELEVANT)

    def test_bare_watch_word_is_only_possibly_relevant_not_relevant(self):
        state, evidence = classify_news(
            "samsung", "Samsung Urges Customers to Watch Out for Rising Phishing Scams This Holiday Season",
            ("Mobile", "Security"),
        )
        self.assertEqual(state, NewsClassification.POSSIBLY_SMARTWATCH_RELEVANT)
        self.assertTrue(any("weak_signal:watch" in e for e in evidence))

    def test_unrelated_article_is_not_relevant(self):
        state, _ = classify_news(
            "samsung", "Not All Ice Is Created Equal: Discover the Difference With Samsung's Ice-Making Technology",
            ("Home Appliances", "Bespoke AI Refrigerator"),
        )
        self.assertEqual(state, NewsClassification.NOT_SMARTWATCH_RELEVANT)


if __name__ == "__main__":
    unittest.main()
