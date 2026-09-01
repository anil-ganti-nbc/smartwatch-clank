# Qualification provenance and reset ledger

Smartwatch qualification evidence is stored in the target SQLite database. This
is a source-level contract; it does not perform deployment or host verification.

## Authority vocabulary

The tracked scheduler launchers (`soak_runner.py`, `deploy/run.sh`, and
`deploy/deploy_run.sh`) pass `SCHEDULED` into the canonical runner. The CLI and dashboard collection paths pass `MANUAL`. Direct library callers and legacy
rows are `UNKNOWN`. Smartwatch has no collector execution path that can
authoritatively assert `DEPLOY` or `RECOVERY`, so those values are not claimed.

The trigger is persisted in `runs.execution_provenance` and in the
qualification event ledger. A missing or unrecognized value normalizes to
`UNKNOWN`; it is never promoted to a trusted category downstream.

## Material identity

Each execution receives a stable `swq1-...` identity derived from:

- application version;
- resolved configuration/scope fingerprint;
- baked source revision;
- SQLite schema version;
- execution scope; and
- the qualification-contract version.

Host, process, timestamp, cycle UUID, and observation content are not inputs.
A known source revision and resolved configuration are required for a
qualification gate to be eligible. Local or unbaked `unknown` identity stays
visible and fails closed.

## Ordering and persistence

`Runner` creates an execution UUID, carries its `RunProvenance`, computes the
material identity, and calls `SQLiteStore.prepare_qualification` before reading
the prior healthy catalogue. An unchanged identity reuses the active epoch. A
changed identity appends a new epoch and a `RESET` event containing the prior
epoch/material identity, new identity, reason, execution ID, provenance, and
time. The first execution in a database records `EPOCH_STARTED`.

The terminal `runs` row and a separate `TERMINAL` qualification event are
written after processing. Their uniqueness key is `(collector, event_type,
execution_id)`, so a reset and terminal fact can coexist for one execution
while terminal recording remains idempotent. Catalogue history is queried by
the active qualification epoch during a run, so the first changed execution
cannot consume an old-epoch catalogue as its baseline. Old rows retain null
qualification columns and are not backfilled.

The migration is additive (`SQLiteStore.SCHEMA_VERSION = 3`): three nullable
run columns and the `qualification_epochs`/`qualification_events` tables are
created without rewriting existing history. The continuity JSONL epoch remains
an independent data-loss/restore record and is not reused as qualification
state.

## Gate

`SQLiteStore.qualification_gate` reads only the latest active epoch and its
latest terminal evidence. It requires a matching material identity, healthy
terminal evidence, a supported non-`UNKNOWN` trigger, and trustworthy material
components. Missing, stale, divergent, or untrusted evidence returns an
ineligible decision. No delivery or notification path fabricates provenance.
