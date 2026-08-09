"""Tests for the Git-revision provenance fields in runtime_bridge.

Covers SMARTWATCH_CLANK_SOURCE_REVISION handling only -- mirrors the pattern
already proven and tested on OEM Radar / Chinese Tech Wire / Feature Phone Clank.
"""

from __future__ import annotations

import os
import unittest

from smartwatch_clank import runtime_bridge


class SourceRevisionTests(unittest.TestCase):
    def setUp(self):
        self._prior = os.environ.pop("SMARTWATCH_CLANK_SOURCE_REVISION", None)

    def tearDown(self):
        if self._prior is not None:
            os.environ["SMARTWATCH_CLANK_SOURCE_REVISION"] = self._prior
        else:
            os.environ.pop("SMARTWATCH_CLANK_SOURCE_REVISION", None)

    def test_defaults_to_unknown_without_env_var(self):
        self.assertEqual(runtime_bridge._source_revision(), "unknown")
        self.assertEqual(runtime_bridge._source_revision_short(), "unknown")

    def test_reflects_full_sha_from_env(self):
        full_sha = "8bcc678ea518a3fc724cba867beeaf54e90725b8"
        os.environ["SMARTWATCH_CLANK_SOURCE_REVISION"] = full_sha
        self.assertEqual(runtime_bridge._source_revision(), full_sha)
        self.assertEqual(runtime_bridge._source_revision_short(), full_sha[:12])

    def test_identity_includes_source_revision(self):
        full_sha = "8bcc678ea518a3fc724cba867beeaf54e90725b8"
        os.environ["SMARTWATCH_CLANK_SOURCE_REVISION"] = full_sha
        result = runtime_bridge.identity()
        self.assertEqual(result["source_revision"], full_sha)
        self.assertEqual(result["source_revision_short"], full_sha[:12])

    def test_identity_reports_unknown_without_env_var(self):
        result = runtime_bridge.identity()
        self.assertEqual(result["source_revision"], "unknown")
        self.assertEqual(result["source_revision_short"], "unknown")


if __name__ == "__main__":
    unittest.main()
