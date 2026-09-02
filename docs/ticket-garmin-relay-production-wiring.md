# Ticket: Garmin egress relay — production lane wiring gap

Status: RESOLVED 2026-08-30 — production runner wiring fixed (deploy/deploy_run.sh exports the
canonical default; regression-tested). Soak path was never affected. Deployment contract pinned
by tests/test_deployment_garmin_relay.py.

## Problem statement

The production cron wrapper does not propagate `SMARTWATCH_CLANK_GARMIN_PROXY`, so a future
production run of a `www.garmin.com` collector would silently bypass the NAS egress relay and
attempt direct retrieval (guaranteed Cloudflare 403).

## Runtime paths (exact)

1. **Soak (current Garmin pair path) — NO ISSUE:** systemd
   `smartwatch-clank-soak.service` → `deploy/run.sh` line 50:
   `-e SMARTWATCH_CLANK_GARMIN_PROXY="${SMARTWATCH_CLANK_GARMIN_PROXY-http://host.docker.internal:18889}"`
   Unset ⇒ canonical default to the `garmin-relay-forwarder` socat bridge (127.0.0.1:18888 tunnel ←
   0.0.0.0:18889). Explicit `SMARTWATCH_CLANK_GARMIN_PROXY=""` still disables it by design.
2. **Production (future path) — DEFECT:** deploy-user cron `50 1-23/2 * * *` →
   `deploy_run.sh` → `docker compose -f docker-compose.staging.yml run --rm smartwatch-clank`
   with compose passthrough `SMARTWATCH_CLANK_GARMIN_PROXY: ${SMARTWATCH_CLANK_GARMIN_PROXY:-}`.
   The wrapper exports only `IMAGE_TAG`; the variable is unset ⇒ empty string ⇒ relay explicitly
   disabled ⇒ direct retrieval.

## Observed evidence

- Relay chain verified healthy (socat forwarder up 24h+; sshd tunnel on 127.0.0.1:18888; socat on
  0.0.0.0:18889; ufw 18889 open to the docker subnets).
- 2026-08-30 12:42Z natural soak cycle: both www.garmin.com collectors failed with relay `URLError`
  timeout (transient; direct-path `garmin_updates` succeeded in the same window — failure isolation
  works, and relay failure surfaces as FAILED, never as silent quiet success).

## Canonical env source

The relay address is **not a secret** (host loopback/bridge address). Canonical value =
`http://host.docker.internal:18889`, defined in one place and mirrored, never committed per-host
state, never a credentials blob.

## Minimum viable repair

Mirror run.sh's line in `deploy_run.sh`:
`export SMARTWATCH_CLANK_GARMIN_PROXY="${SMARTWATCH_CLANK_GARMIN_PROXY-http://host.docker.internal:18889}"`
before the compose invocation (same default-if-unset semantics; explicit empty still disables).
Nothing else changes — no compose, no image, no timer edits.

## Explicit non-goals

- No change to the soak path (undisturbed while the Garmin pair soaks).
- No promotion of the Garmin pair in this repair.
- No secrets management rework.

## Tests required

- An env-resolution unit test asserting the production run path resolves the relay for
  `www.garmin.com` collectors when the variable is unset, and honours explicit disable.
- A deployment verification command/post-deploy check that prints the resolved proxy for the
  garmin collectors (e.g. extend the existing revision/identity verification output).

## Failure behaviour to preserve

Relay absent/unreachable ⇒ collectors FAIL loudly (`SourceHostBlockedError` / `ProxyUnreachableError`
/ URLError) and surface as failed runs. Never degrade to silent empty success. A relay stall must
remain distinguishable from a quiet source in `collector_health`.

## Soak required after repair

None for the repair itself (deployment plumbing). The Garmin pair's promotion still requires their
own soak exit condition (12–20 clean natural 6-hour cycles, no relay-caused failure in the trailing
qualification window) evaluated **with production-relay wiring in place**.

## Rollback considerations

One-line wrapper revert; no state, no image, no schedule impact.

## Risk level

LOW.

---
2026-09-02 reliability addendum: the relay outage root cause was the NAS
tunnel container's ssh dying silently on ~6h NAT idle gaps (no effective
client keepalive in the deployed command) and hanging as a dead tunnel
while sshd held the stale `-R 18888` listener. Hetzner sshd now sets
`ClientAliveInterval 15` / `ClientAliveCountMax 3` for `deploy`
(kill->auto-recovery proven within 18s; see docs/garmin-egress-relay.md).
REMAINING OPERATOR ACTION (requires NAS docker admin): add
`-o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o
ExitOnForwardFailure=yes` to the tunnel container's ssh command so the
client side self-heals silent NAT deaths too.
