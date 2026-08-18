import unittest

from smartwatch_clank.collectors.registry import build_registry
from smartwatch_clank.core.models import CollectorTier
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

    def test_production_selection_is_oem_agnostic(self):
        def register_oem_a(registry):
            registry.register(DummyCollector("oem_a_prod", tier=CollectorTier.PRODUCTION))

        def register_oem_b(registry):
            registry.register(DummyCollector("oem_b_prod", tier=CollectorTier.PRODUCTION))

        registry = build_registry((("oem_a", register_oem_a), ("oem_b", register_oem_b)))
        selected = registry.selected(CollectorTier.PRODUCTION, ("oem_a_prod", "oem_b_prod"))
        self.assertEqual({c.name for c in selected}, {"oem_a_prod", "oem_b_prod"})


if __name__ == "__main__":
    unittest.main()
