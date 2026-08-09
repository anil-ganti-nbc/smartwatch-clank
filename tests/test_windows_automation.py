import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from smartwatch_clank.cli import main
from smartwatch_clank.core.models import CollectorTier
from smartwatch_clank.core.registry import CollectorRegistry
from tests.helpers import DummyCollector
from smartwatch_clank import soak_runner


ROOT = Path(__file__).resolve().parents[1]


class WindowsAutomationTests(unittest.TestCase):
    def test_scheduled_runner_uses_production_mode_and_canonical_cli(self):
        script = (ROOT / "scripts" / "run_samsung_production_soak.ps1").read_text(encoding="utf-8")
        self.assertIn("-m smartwatch_clank.soak_runner", script)
        self.assertNotIn("--mode experimental", script)
        self.assertIn(".venv\\Scripts\\python.exe", script)
        portable = (ROOT / "src" / "smartwatch_clank" / "soak_runner.py").read_text(encoding="utf-8")
        self.assertIn('"smartwatch_clank.cli", "run", "--mode", "production"', portable)
        self.assertIn("load_runtime_config().database.resolve()", portable)

    def test_installation_is_one_task_with_twelve_daily_triggers_and_ignore_new(self):
        script = (ROOT / "scripts" / "install_samsung_soak_task.ps1").read_text(encoding="utf-8")
        self.assertIn("Smartwatch Clank - Samsung Production Soak", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)
        self.assertIn("0,2,4,6,8,10,12,14,16,18,20,22", script)

    def test_uninstall_does_not_remove_evidence(self):
        script = (ROOT / "scripts" / "uninstall_samsung_soak_task.ps1").read_text(encoding="utf-8")
        self.assertIn("Unregister-ScheduledTask", script)
        self.assertNotIn("Remove-Item", script)
        self.assertNotIn("sqlite3", script.lower())

    def test_failed_scheduled_cycle_has_nonzero_cli_exit(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector(
            "samsung_product_catalogue", tier=CollectorTier.PRODUCTION, error=RuntimeError("scheduled failure")
        ))
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                status = main([
                    "--database", str(Path(directory) / "scheduled.sqlite3"), "run", "--mode", "production", "--no-lock"
                ], registry)
            self.assertEqual(status, 1)

    def test_portable_runner_invokes_canonical_production_command_and_logs(self):
        class FakeProcess:
            stdout = iter(['{"healthy": 4}\n'])

            @staticmethod
            def wait():
                return 0

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "var" / "smartwatch-clank.sqlite3"
            with patch.object(soak_runner, "_project_root", return_value=root), \
                    patch.object(soak_runner, "load_runtime_config", return_value=SimpleNamespace(database=database)), \
                    patch.object(soak_runner.subprocess, "Popen", return_value=FakeProcess()) as popen:
                self.assertEqual(soak_runner.main(), 0)
            command = popen.call_args.args[0]
            self.assertEqual(command[1:], ["-m", "smartwatch_clank.cli", "run", "--mode", "production"])
            logs = list((root / "var" / "logs" / "soak").glob("smartwatch-clank-*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("cycle_finish exit_status=0", logs[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
