import unittest

from smartwatch_clank.collectors.registry import build_registry
from smartwatch_clank.core.models import CollectorTier, RunScope
from tests.helpers import DummyCollector


class MultiOemRegistryTests(unittest.TestCase):
    def test_two_oems_coexist_in_one_registry(self):
        def register_oem_a(registry):
            registry.register(DummyCollector("oem_a_catalogue"))

        def register_oem_b(registry):
            registry.register(DummyCollector("oem_b_catalogue"))

        registry = build_registry((("oem_a", register_oem_a), ("oem_b", register_oem_b)))
        self.assertEqual(
            {c.name for c in registry.all()}, {"oem_a_catalogue", "oem_b_catalogue"},
        )
        self.assertEqual(registry.registration_failures, {})

    def test_one_oems_registration_failure_does_not_block_the_other(self):
        def register_broken(registry):
            raise RuntimeError("bad OEM config")

        def register_working(registry):
            registry.register(DummyCollector("working_catalogue"))

        registry = build_registry((("broken_oem", register_broken), ("working_oem", register_working)))
        self.assertEqual([c.name for c in registry.all()], ["working_catalogue"])
        self.assertIn("broken_oem", registry.registration_failures)
        self.assertIn("bad OEM config", registry.registration_failures["broken_oem"])

    def test_default_registry_still_registers_samsung_collectors_unchanged(self):
        from smartwatch_clank.collectors import default_registry

        registry = default_registry()
        names = {c.name for c in registry.all()}
        self.assertIn("samsung_product_catalogue", names)
        self.assertIn("samsung_support_in", names)
        self.assertEqual(registry.registration_failures, {})

    def test_default_registry_registers_all_stage_c_collectors_alongside_existing_ones(self):
        from smartwatch_clank.collectors import default_registry

        registry = default_registry()
        names = {c.name for c in registry.all()}
        self.assertEqual(registry.registration_failures, {})
        stage_c_names = {
            "garmin_catalogue", "garmin_updates", "amazfit_catalogue", "amazfit_official_news",
            "coros_support", "coros_updates", "coros_official_news", "dcrainmaker_specialist",
        }
        self.assertTrue(stage_c_names.issubset(names))
        # Existing Samsung production + all 4 original news collectors are
        # untouched by Stage C's registration additions.
        self.assertTrue({
            "samsung_product_catalogue", "samsung_support_in", "samsung_support_gb", "samsung_support_de",
            "samsung_official_news", "google_official_news", "garmin_official_news", "apple_official_news",
        }.issubset(names))
        self.assertEqual(len(names), len(stage_c_names) + 8)  # 4 Samsung production + 4 original news collectors; Wave 2 adds garmin_updates + dcrainmaker_specialist  # 4 Samsung production + 4 original news collectors

    def test_production_selection_is_oem_agnostic(self):
        def register_oem_a(registry):
            registry.register(DummyCollector("oem_a_prod", tier=CollectorTier.PRODUCTION))

        def register_oem_b(registry):
            registry.register(DummyCollector("oem_b_prod", tier=CollectorTier.PRODUCTION))

        registry = build_registry((("oem_a", register_oem_a), ("oem_b", register_oem_b)))
        selected = registry.selected(RunScope.PRODUCTION, ("oem_a_prod", "oem_b_prod"))
        self.assertEqual({c.name for c in selected}, {"oem_a_prod", "oem_b_prod"})


if __name__ == "__main__":
    unittest.main()
