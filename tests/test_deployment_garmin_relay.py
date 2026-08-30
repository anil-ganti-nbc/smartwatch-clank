"""Deployment-contract tests for the Garmin egress relay wiring (2026-08-30).

Background (docs/ticket-garmin-relay-production-wiring.md): the soak runner
(deploy/run.sh) defaults SMARTWATCH_CLANK_GARMIN_PROXY to the canonical
forwarder address, but the production cron wrapper originally did not — so a
future promoted www.garmin.com collector would have resolved the proxy as
empty and fetched www.garmin.com directly (Cloudflare-blocked from this
host). These tests pin the deployment contract from the tracked files:

- BOTH lane runners default the proxy to the canonical forwarder address
  when the variable is unset;
- both use the `${VAR-default}` form so an explicit empty disable is
  honoured (documented operator escape hatch);
- the compose service passes the variable through to the container;
- the only committed relay address is the host-internal bridge/loopback one
  (never an external endpoint or credential).

These are content assertions on shell deployment glue: the repo has no
deployment-test framework, and the wrappers are the only place the lane
default can live. unittest.TestCase style so the canonical
`python -m unittest discover -s tests` runner collects them.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CANONICAL_RELAY = "http://host.docker.internal:18889"
DEFAULT_RULE = "${SMARTWATCH_CLANK_GARMIN_PROXY-http://host.docker.internal:18889}"


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class GarminRelayWiringTests(unittest.TestCase):
    def test_soak_runner_defaults_relay_canonically(self):
        run_sh = _read("deploy/run.sh")
        self.assertIn(DEFAULT_RULE, run_sh, "deploy/run.sh must default "
                      "SMARTWATCH_CLANK_GARMIN_PROXY to the canonical forwarder address")
        self.assertIn("-e SMARTWATCH_CLANK_GARMIN_PROXY=", run_sh)

    def test_production_runner_defaults_relay_canonically(self):
        deploy_run = _read("deploy/deploy_run.sh")
        self.assertIn(DEFAULT_RULE, deploy_run, "deploy/deploy_run.sh (production "
                      "cron wrapper) must carry the same canonical relay default "
                      "as deploy/run.sh")
        self.assertIn("export SMARTWATCH_CLANK_GARMIN_PROXY=", deploy_run)

    def test_default_form_preserves_explicit_disable(self):
        """`${VAR-default}` (unset -> default) vs `${VAR:-default}` (unset OR
        empty -> default): the operators' documented escape hatch is an
        explicit empty value, so the `-` form is part of the contract in both
        runners."""
        for rel in ("deploy/run.sh", "deploy/deploy_run.sh"):
            text = _read(rel)
            self.assertIn(DEFAULT_RULE, text, rel)
            self.assertNotIn(
                "${SMARTWATCH_CLANK_GARMIN_PROXY:-http://host.docker.internal:18889}",
                text, f"{rel} must keep the unset-only default form (explicit empty disable)")

    def test_compose_passes_proxy_through_to_container(self):
        compose = _read("docker-compose.staging.yml")
        self.assertRegex(
            compose, r"SMARTWATCH_CLANK_GARMIN_PROXY:\s*\$\{SMARTWATCH_CLANK_GARMIN_PROXY",
            "docker-compose.staging.yml must pass SMARTWATCH_CLANK_GARMIN_PROXY "
            "through to the container environment")
        self.assertIn("host.docker.internal:host-gateway", compose,
                      "the compose service must map host.docker.internal for the relay address")

    def test_no_relay_credentials_committed(self):
        """The relay is an unauthenticated host-internal bridge address. Assert
        the committed relay references stay host-internal (no external
        endpoint, no bearer/token material) across the deployment surfaces."""
        for rel in ("deploy/run.sh", "deploy/deploy_run.sh", "docker-compose.staging.yml"):
            text = _read(rel)
            for match in re.findall(r"https?://[^\s\"')}]+", text):
                host = match.split("//", 1)[1].split("/", 1)[0]
                self.assertEqual(host, "host.docker.internal:18889",
                                 f"{rel}: unexpected relay host {host}")
            self.assertIsNone(re.search(r"(?i)(bearer|authorization|token\s*=)", text), rel)


if __name__ == "__main__":
    unittest.main()
