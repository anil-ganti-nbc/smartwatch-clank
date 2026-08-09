import unittest

from smartwatch_clank.core.models import CollectorTier
from smartwatch_clank.core.registry import CollectorRegistry
from tests.helpers import DummyCollector


class RegistryTests(unittest.TestCase):
    def test_registration_is_unique_and_sorted(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("zeta"))
        registry.register(DummyCollector("alpha"))
        self.assertEqual([c.name for c in registry.all()], ["alpha", "zeta"])
        with self.assertRaises(ValueError):
            registry.register(DummyCollector("alpha"))

    def test_production_requires_tier_and_allowlist(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("ready", tier=CollectorTier.PRODUCTION))
        registry.register(DummyCollector("lab", tier=CollectorTier.EXPERIMENTAL))
        self.assertEqual(registry.selected(CollectorTier.PRODUCTION, ()), ())
        self.assertEqual([c.name for c in registry.selected(CollectorTier.PRODUCTION, ("ready", "lab"))], ["ready"])


if __name__ == "__main__":
    unittest.main()

