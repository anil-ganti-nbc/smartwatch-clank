"""Regression coverage for explicit RunScope selection semantics.

Written after a real deployment bug: the old CollectorRegistry.selected()
treated `experimental` as "every registered collector" (self.all()),
so the four production-tier Samsung collectors silently ran through both
the production cron AND the new experimental soak. RunScope makes the
three intents explicit and independent -- see core/models.py's RunScope
docstring for the design rationale.
"""

import unittest

from smartwatch_clank.core.models import CollectorTier, RunScope
from smartwatch_clank.core.registry import CollectorRegistry
from tests.helpers import DummyCollector


class RunScopeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.registry = CollectorRegistry()
        self.registry.register(DummyCollector("samsung_a", tier=CollectorTier.PRODUCTION))
        self.registry.register(DummyCollector("samsung_b", tier=CollectorTier.PRODUCTION))
        self.registry.register(DummyCollector("news_a", tier=CollectorTier.EXPERIMENTAL))
        self.registry.register(DummyCollector("news_b", tier=CollectorTier.EXPERIMENTAL))

    def test_production_selects_only_allowlisted_production_collectors(self):
        selected = self.registry.selected(RunScope.PRODUCTION, ("samsung_a",))
        self.assertEqual([c.name for c in selected], ["samsung_a"])

    def test_production_tier_collector_not_in_allowlist_does_not_run(self):
        selected = self.registry.selected(RunScope.PRODUCTION, ("samsung_a",))
        self.assertNotIn("samsung_b", [c.name for c in selected])

    def test_experimental_selects_only_experimental_tier_collectors(self):
        selected = self.registry.selected(RunScope.EXPERIMENTAL)
        self.assertEqual([c.name for c in selected], ["news_a", "news_b"])

    def test_experimental_excludes_production_tier_collectors_even_if_allowlisted(self):
        # The allowlist must never leak into experimental selection -- passing
        # it here should have zero effect, proving EXPERIMENTAL is tier-only.
        selected = self.registry.selected(RunScope.EXPERIMENTAL, ("samsung_a", "samsung_b"))
        names = [c.name for c in selected]
        self.assertNotIn("samsung_a", names)
        self.assertNotIn("samsung_b", names)
        self.assertEqual(names, ["news_a", "news_b"])

    def test_all_scope_selects_both_tiers(self):
        selected = self.registry.selected(RunScope.ALL)
        self.assertEqual(
            {c.name for c in selected}, {"samsung_a", "samsung_b", "news_a", "news_b"},
        )

    def test_empty_production_allowlist_runs_zero_production_collectors(self):
        selected = self.registry.selected(RunScope.PRODUCTION, ())
        self.assertEqual(selected, ())

    def test_future_experimental_collector_automatically_joins_experimental_soak(self):
        self.registry.register(DummyCollector("news_c_not_yet_allowlisted", tier=CollectorTier.EXPERIMENTAL))
        selected = self.registry.selected(RunScope.EXPERIMENTAL)
        # No allowlist edit anywhere -- pure tier membership is sufficient.
        self.assertIn("news_c_not_yet_allowlisted", [c.name for c in selected])

    def test_future_production_collector_does_not_auto_join_without_allowlist_approval(self):
        self.registry.register(DummyCollector("samsung_c_new", tier=CollectorTier.PRODUCTION))
        selected = self.registry.selected(RunScope.PRODUCTION, ("samsung_a",))
        # Registered and production-tier, but never allowlisted -- must not run.
        self.assertNotIn("samsung_c_new", [c.name for c in selected])
        # It also must not sneak into the experimental soak by virtue of being
        # production-tier -- experimental selection is unaffected by its existence.
        self.assertNotIn("samsung_c_new", [c.name for c in self.registry.selected(RunScope.EXPERIMENTAL)])


class DefaultRegistryScopeTests(unittest.TestCase):
    """Proves the actual bug is fixed against the real, deployed registry."""

    def test_experimental_soak_excludes_the_four_real_samsung_production_collectors(self):
        from smartwatch_clank.collectors import default_registry

        registry = default_registry()
        experimental = {c.name for c in registry.selected(RunScope.EXPERIMENTAL)}
        for samsung_production_name in (
            "samsung_product_catalogue", "samsung_support_in", "samsung_support_gb", "samsung_support_de",
        ):
            self.assertNotIn(samsung_production_name, experimental)
        self.assertEqual(
            experimental,
            {
                "samsung_official_news", "google_official_news", "garmin_official_news", "apple_official_news",
                "garmin_catalogue", "garmin_updates", "amazfit_official_news", "amazfit_catalogue",
                "coros_support", "coros_updates", "coros_official_news",
                "dcrainmaker_specialist",
            },
        )

    def test_production_scope_is_unchanged_four_samsung_collectors_only(self):
        from smartwatch_clank.collectors import default_registry

        registry = default_registry()
        allowlist = (
            "samsung_product_catalogue", "samsung_support_in", "samsung_support_gb", "samsung_support_de",
        )
        production = {c.name for c in registry.selected(RunScope.PRODUCTION, allowlist)}
        self.assertEqual(production, set(allowlist))


if __name__ == "__main__":
    unittest.main()
