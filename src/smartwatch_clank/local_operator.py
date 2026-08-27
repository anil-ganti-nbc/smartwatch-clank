"""Launcher-scoped local operator mutation authority (Phase 0 reconciliation).

Smartwatch Clank's dashboard was fail-closed by construction: every POST to
`/api/local-collection/run` returned 403 unconditionally, because no
"authenticated profile" existed yet (see README's Phase 0 banner). Nothing
in this repository ever defined what that profile should look like for a
single-operator local desktop tool -- building real multi-user auth for a
loopback-only field-test dashboard would be the wrong shape of fix.

This module is the narrow, deliberate restoration, modeled directly on Watch
Clank's `app/local_operator.py` (same fleet, same problem, already reviewed
and shipped there):

- Authority is installed ONLY by an explicit supported launcher path --
  `native/windows/launcher.py` / `native/macos/launcher.py` calling
  `dashboard.serve(..., local_operator=True)`. Nothing else in this
  repository ever passes that flag, so any other way of starting the
  dashboard (bare `serve()`, the test suite, a hypothetical future
  `python -m smartwatch_clank.dashboard`) stays fail-closed by default --
  the "authenticated profile" IS "you launched this via the vetted local
  desktop launcher, not by hand-wiring the HTTP server yourself."
- The authorizer re-proves, per request: loopback client address AND a
  loopback/localhost Host header. Forwarded headers (X-Forwarded-For,
  X-Real-IP, X-Forwarded-Host) are deliberately never consulted -- there is
  no reverse-proxy architecture here, and trusting them would be spoofable
  by anything that can reach the port at all.
- Only an explicit, closed-ended allowlist of operator-safe routes is
  authorized. A new mutation route must be added HERE deliberately; nothing
  is inherited implicitly by prefix-matching:
    POST /api/qc/decide/{discovery_id}         one QC decision
    POST /api/local-collection/run/{collector}  run one finalized collector
    POST /api/local-collection/run-all          run all finalized collectors
  Everything else -- including any future POST -- stays 403.

Notification delivery is untouched by this module: Discord remains
unimplemented (`notifications/discord.py` raises `NotImplementedError`) and
nothing here changes that, so an authorized local mutation can write to the
local database and the separate QC archive but can never send anything
off-machine.
"""
from __future__ import annotations

import ipaddress
import re

# Operator-safe mutation routes. Anchored and closed-ended on purpose --
# "starts with" matching would silently authorize a future route that was
# never reviewed for this authority.
_LOCAL_OPERATOR_ROUTES = (
    re.compile(r"^/api/qc/decide/\d+$"),
    re.compile(r"^/api/local-collection/run/[A-Za-z0-9_]+$"),
    re.compile(r"^/api/local-collection/run-all$"),
)


def _loopback(value: str | None) -> bool:
    """A literal loopback IP or the 'localhost' name. Never consults
    forwarded headers -- there is no proxy in front of this server."""
    if not value:
        return False
    value = value.strip().strip("[]")
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value.lower() == "localhost"


def request_is_local_operator_mutation(*, client_host: str | None, host_header: str | None,
                                        method: str, path: str) -> bool:
    """True only for a POST from a loopback client, to a loopback Host
    header, on the explicit operator allowlist above."""
    if method != "POST":
        return False
    if not _loopback(client_host):
        return False
    if not _loopback((host_header or "").rsplit(":", 1)[0]):
        return False
    return any(pattern.match(path) for pattern in _LOCAL_OPERATOR_ROUTES)
