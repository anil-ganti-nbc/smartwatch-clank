import unittest

from smartwatch_clank.core.scope import Scope, ScopeDecision
from smartwatch_clank.paths import config_path


class ScopeTests(unittest.TestCase):
    def test_scope_decisions_are_conservative(self):
        scope = Scope.load(config_path("scope.yaml"))
        self.assertEqual(scope.decide("Samsung", "smartwatch"), ScopeDecision.INCLUDE)
        self.assertEqual(scope.decide("Samsung", "fitness_band"), ScopeDecision.REVIEW)
        self.assertEqual(scope.decide("Samsung", "smart_ring"), ScopeDecision.EXCLUDE)
        self.assertEqual(scope.decide("Unknown OEM", "smartwatch"), ScopeDecision.REVIEW)


if __name__ == "__main__":
    unittest.main()

