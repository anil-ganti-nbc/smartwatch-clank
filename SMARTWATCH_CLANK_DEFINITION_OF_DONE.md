# Smartwatch Clank — Definition of Done

## V1 scope

V1 is Samsung smartwatch intelligence only. It monitors Samsung product
catalogue evidence and Samsung support evidence in IN, GB and DE; US/KR
catalogue research remains contextual, not a separate production claim. Other
manufacturers are out of scope unless explicitly approved later.

## Current production state

The four Samsung collectors run as the production allowlist on a two-hour
Hetzner cron. They share an atomic database-adjacent lock and persist durable
SQLite observations, per-region identity dimensions, collector health,
reconciliation records and candidate-event evidence. `SMARTWATCH_CLANK_HOST_ID`
is explicitly set to `hetzner-clank-fleet-01`, preventing ephemeral Docker
container hostnames from fabricating migration/gap records. A real host change
records the elapsed gap without resetting baseline history.

Support evidence proves official support presence and model/region evidence;
it does not by itself prove current retail availability. Catalogue evidence is
a merchandising snapshot, not a device-discontinuation signal. Failed,
partial, or unhealthy collectors preserve their last healthy observations;
regional failures are isolated. Reconciliation distinguishes exact catalogue
and support matches, support-only regional SKUs, and ambiguous support-only
records. Discord delivery is intentionally unimplemented; CLI/database are
the operator surface.

## V1 acceptance gate

Samsung additions, returns, supported specification/availability changes,
and reconciliation evidence must be durable; unhealthy collection must not
produce disappearance; scheduled runs and locking must remain proven; and
the canonical suite must pass. Owner UX validation remains **OWNER FIELD TEST
— PENDING**.

## Backlog and Stage A

- **P0 fixed:** configuration-provenance test no longer assumes Windows path
  separators; canonical tests pass on macOS.
- **P1:** continue natural Samsung soak evidence; no current runtime change.
- **P2:** richer availability semantics and delivery policy.
- **P3:** additional OEMs and Unified/Product Intelligence work.
- **Owner decision:** whether post-v1 scope expands beyond Samsung.
- **Expansion Stage A (approved, shared infrastructure only, 2026-08-18):**
  source classes, generic additive evidence/timeline tables, portable run
  provenance (run UUID, config fingerprint, git revision, schema version),
  and an OEM-agnostic collector registry landed with zero change to the
  production Samsung path (still the only collectors, still the only
  production allowlist entries). Full report:
  `docs/expansion-stage-a-report.md`. Not yet deployed to Hetzner — see that
  report's deployment-status section.
