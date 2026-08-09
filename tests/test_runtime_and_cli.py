import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from smartwatch_clank.cli import main
from smartwatch_clank.core.registry import CollectorRegistry
from smartwatch_clank.runtime_bridge import identity


class RuntimeAndCliTests(unittest.TestCase):
    def test_identity_is_machine_readable(self):
        result = identity()
        self.assertEqual(result["service"], "smartwatch-clank")
        self.assertEqual(result["stage"], 2)
        self.assertTrue(result["live_collectors_enabled"])
        self.assertFalse(result["notifications_enabled"])

    def test_production_cli_with_empty_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["--database", str(Path(directory) / "cli.sqlite3"), "run", "--mode", "production"], CollectorRegistry())
            self.assertEqual(status, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["collectors_run"], 0)
            self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
