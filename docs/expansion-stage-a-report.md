# Smartwatch Clank — Expansion Stage A report

## Scope

Shared infrastructure only, per the approved multi-OEM expansion spec's
mandate to proceed in waves. No new OEM collectors were implemented — Samsung
remains the only working collector, behavior unchanged. This stage exists so
later OEM/source-class collectors (Stage B: official news; Stage C+: Google,
Garmin, Apple, Wave B) have somewhere to plug in without another schema or
registry rewrite.

## What was built

- **Source classes**: `SourceClass` enum (`core/models.py`) — `PRODUCT_CATALOGUE`,
  `SUPPORT` (equal to the existing `source_kind` strings, so nothing that
  reads `Observation.source_kind` today changed behavior), plus new
  `OFFICIAL_NEWS`, `SOFTWARE_UPDATE`, `COMPANION_APP`, `CERTIFICATION`.
  `Observation` gained `source_class` and `oem` fields (both optional,
  default `None` — existing call sites unaffected).
- **Generic cross-source evidence + timeline**: new additive tables
  `evidence_records` (oem, source_class, identity, region, confidence,
  editorial_level, source_url, first_seen, last_seen, payload_json) and
  `evidence_timeline` (ordered events per evidence record). `first_seen`
  is never overwritten by a later write to the same `(source_class,
  identity)` — tested directly. The existing Samsung-specific tables
  (`samsung_reconciliation_runs`, `samsung_reconciliation_records`,
  `samsung_candidate_events`) were deliberately **not** touched or renamed:
  they hold real production history, and there's no second OEM's
  reconciliation logic yet to generalize them against. No collector writes
  to the new evidence tables yet — this stage proves the write/read/
  first-seen-preservation contract only.
- **Portable run provenance**: `runs` table gained `run_uuid`,
  `app_version`, `schema_version_at_run`, `config_fingerprint`,
  `git_revision` columns (additive `ALTER TABLE`, existing guard pattern).
  A new `schema_version` table tracks the current schema version (now `2`).
  `Runner` generates a UUID per run and threads a new `RunProvenance`
  dataclass (app version, config fingerprint, git revision) through to
  persistence. `cli identity` and `cli run` now carry this data end to end.
- **Config fingerprint**: `configuration.config_fingerprint()` (internally
  `_fingerprint`) hashes the merged `config.yaml` + `scope.yaml` content
  (sha256, truncated to 16 hex chars) — stable across runs, changes when
  either file's content changes.
- **OEM-agnostic collector registry**: `collectors/registry.py` introduces
  `OEM_REGISTRATIONS`, a list of `(oem_name, register_fn)` pairs, and
  `build_registry()`, which registers each OEM's collectors with per-OEM
  failure isolation (`registry.registration_failures`) — a bug in one
  OEM's registration cannot prevent another OEM's collectors from
  registering. `collectors/__init__.py` is now a two-line wrapper.
  Samsung's actual registration code moved unchanged into
  `_register_samsung`.
- **Deploy scaffolding (inert)**: `deploy/crontab.example`,
  `deploy/smartwatch-clank-soak.service.example` +
  `.timer.example`, `deploy/run.sh` (host-side wrapper reading
  `.deployed-id`), and `scripts/deploy_hetzner.sh` (local-driven deploy:
  clean-tree check, GitHub ancestry check, remote backup, checkout, build,
  three-way SHA verification, `.deployed-id` update, one verification
  cycle). None of these were executed against Hetzner this session.

## What was intentionally left alone

`collectors/samsung/*`, `intelligence/samsung.py`, `classifiers/__init__.py`
(still a stub), `notifications/discord.py` (still dormant), `dashboard.py`,
`local_collection.py`, `config/config.yaml`'s `production_allowlist` (still
exactly the 4 Samsung collectors), `scripts/*.ps1` (Windows launcher
retirement is a later deployment-stage concern). `cli.py`'s Samsung-specific
reconciliation dispatch (the `samsung_support_` name-prefix check) is
unchanged — generalizing it needs a second OEM with real reconciliation
logic to design against, which doesn't exist yet.

## Tests

- Before Stage A: 55 tests passing.
- After Stage A: **72 tests passing, 0 regressions** (17 new: 4 in
  `test_source_class.py`, 5 in `test_run_provenance.py`, 5 in
  `test_evidence_store.py`, 4 in `test_collector_registry_multi_oem.py`).
- New coverage: source-class/observation round-tripping, run provenance
  persistence and per-run UUID uniqueness, config-fingerprint stability,
  evidence first-seen preservation (including independent first-seen per
  source class for the same identity), multi-OEM registry coexistence and
  per-OEM registration failure isolation, and confirmation that
  `default_registry()` still registers the same Samsung collectors as
  before.

## Database/schema changes

Schema version bumped `(none)` -> `2`, entirely additive:
`schema_version`, `evidence_records`, `evidence_timeline` tables; `runs`
gained 5 nullable columns. Verified against a scratch database (not
`var/smartwatch-clank.sqlite3`) via `cli run --mode experimental
--allow-experimental-database`: all four Samsung collectors registered and
ran unchanged (49 catalogue + 378 support observations, Samsung
reconciliation ran as before with 394 relationships), and the new
provenance columns/`schema_version` table populated correctly.

## Soak participation

None yet — no new collector feeds the generic soak. The existing Samsung
production soak on Hetzner is unaffected; this stage does not touch it.

## Deployment status

```
Local:
    commit:  (see below, pushed after this report)
    tests:   72 passed, 0 failed
    working tree: clean after commit

GitHub:
    branch:  expansion/stage-a-shared-infrastructure
    pushed commit: (see below)

Hetzner:
    deployed commit: unchanged (still the prior production revision)
    database schema: unchanged on the live host (schema v2 exists only
                      in this local/GitHub code; the live database will
                      pick it up additively on the next real deployment)
    timer: unchanged (still the existing 2-hour Docker/cron cadence)
    last run: unchanged

Parity:
    local/GitHub/Hetzner source revisions match: NO (by design this
    session) -- Hetzner deployment was explicitly out of scope per the
    owner's decision; see scripts/deploy_hetzner.sh for the follow-up.
```

## Recommendation

The shared-infrastructure foundation is sound: additive-only schema changes,
zero behavior change to the production Samsung path, full test coverage
green. Two reasonable next steps, either is fine to do first:

1. **Deploy this revision to Hetzner** (run `scripts/deploy_hetzner.sh`
   after review) to prove the additive schema change and provenance
   plumbing against the real production database before building on top of
   it further.
2. **Expansion Stage B** (official-news collector research for Samsung
   Newsroom, Google, Garmin, Apple Newsroom) — the source classes and
   evidence tables this stage built are what those collectors will write
   into.

No architectural problems were found. Proceeding to either is safe.
