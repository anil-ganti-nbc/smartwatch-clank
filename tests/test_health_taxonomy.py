"""Regression coverage for the typed source-health exception taxonomy.

Added for Stage C (see docs/stage-c-report.md, spec section 25): a real
collector, `garmin_official_news`, gets an HTTP 403 from Hetzner but no
existing test proved that a host-blocked failure is recorded distinctly
from a generic bug/parser-failure -- both used to look identical in the
stored run error, a bare exception message. `SourceHostBlockedError`/
`SourceRateLimitedError`/`ParserFailureError` (core/health.py) let a
collector say *why* it failed; this file proves the Runner records that
distinction and doesn't attach a noisy traceback to what is an expected,
typed failure mode.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from smartwatch_clank.core.health import ParserFailureError, SourceHostBlockedError, SourceRateLimitedError
from smartwatch_clank.core.models import RunScope
from smartwatch_clank.core.registry import CollectorRegistry
from smartwatch_clank.core.runner import Runner
from smartwatch_clank.core.store import SQLiteStore
from tests.helpers import DummyCollector


class HealthTaxonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _run_with_error(self, error: Exception):
        registry = CollectorRegistry()
        registry.register(DummyCollector("flaky", error=error))
        return Runner(registry, self.store).run(RunScope.ALL)[0]

    def test_host_blocked_error_is_recorded_by_type_not_generic_message(self):
        outcome = self._run_with_error(SourceHostBlockedError("HTTP 403 fetching https://example.com"))
        self.assertFalse(outcome.healthy)
        self.assertIn("SourceHostBlockedError", outcome.error)

    def test_rate_limited_error_is_recorded_by_type(self):
        outcome = self._run_with_error(SourceRateLimitedError("HTTP 429 fetching https://example.com"))
        self.assertIn("SourceRateLimitedError", outcome.error)

    def test_parser_failure_is_recorded_by_type(self):
        outcome = self._run_with_error(ParserFailureError("no Product JSON-LD found"))
        self.assertIn("ParserFailureError", outcome.error)

    def test_typed_health_errors_do_not_attach_a_noisy_traceback(self):
        # Matches the existing CatalogueHealthError/ValueError convention:
        # an expected, typed failure mode doesn't need a full traceback in
        # the stored error, unlike a genuine bug.
        outcome = self._run_with_error(SourceHostBlockedError("HTTP 403"))
        self.assertNotIn("Traceback", outcome.error)

    def test_unexpected_bug_still_gets_a_traceback(self):
        outcome = self._run_with_error(RuntimeError("a genuine bug"))
        self.assertIn("Traceback", outcome.error)

    def test_a_host_blocked_collector_does_not_affect_other_collectors(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector("blocked", error=SourceHostBlockedError("HTTP 403")))
        from tests.helpers import observation

        registry.register(DummyCollector("healthy", items=(observation("healthy", "watch-1"),)))
        outcomes = Runner(registry, self.store).run(RunScope.ALL)
        self.assertEqual([(o.collector, o.healthy) for o in outcomes], [("blocked", False), ("healthy", True)])


if __name__ == "__main__":
    unittest.main()
