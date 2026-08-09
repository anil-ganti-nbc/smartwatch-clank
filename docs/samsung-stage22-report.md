# Samsung Stage 2.2 production-readiness report

Completion date: 2026-08-09.

## Operational hardening

- Cross-platform atomic lock adjacent to the active database; owner metadata includes PID, host, timestamp, database, and token.
- Live local owners block a second run; dead local owners are reclaimed; context-manager exit releases after handled exceptions.
- Production refuses experimental-named databases unless an explicit debug bypass is supplied.
- Canonical production path: `var/smartwatch-clank.sqlite3`.
- Configuration provenance includes repository defaults, optional `config/local.yaml`, resolved database, and collector-level allowlist.
- Run history retains timestamps/duration, status, observation and discovery counts, warnings, errors, metadata, and previous healthy state.
- Official-source requests receive three bounded attempts; a still-failing page rejects only its regional collector.

## Production scope

- `samsung_product_catalogue`
- `samsung_support_in`
- `samsung_support_gb`
- `samsung_support_de`

Discord remains disabled. No firmware or other OEM collector is enabled.

## Bootstrap and repeat

The first attempt created healthy silent baselines for product (51), Germany support (159), and India support (57). One UK page timed out; the UK collector rejected the complete attempted snapshot and persisted no partial observations. This motivated bounded request retries.

The retry-hardened run established UK’s silent 162-observation baseline while all other collectors repeated unchanged. Reconciliation onboarded UK silently. A final immediate cycle produced identical counts for all four collectors.

Across the production history at hand:

- 12 collector runs: 11 successful, 1 failed initial UK attempt
- Discoveries: 0
- Candidate events: 0
- Warnings: 0
- Latest health: all four `HEALTHY`
- Product: 51
- Germany support: 159
- UK support: 162
- India support: 57

`SM-L305` is visible through generic candidate inspection as six `GLOBAL_UNKNOWN_SUPPORT_MODEL` regional identities. All are onboarding-baseline suppressed and retain their per-source first-seen timestamps.

## Soak recommendation

Ready to begin a controlled long-duration Samsung production soak. The initial transient UK failure is retained as real operational history and recovered cleanly without partial-state replacement. External scheduling and continued health review are still required; autonomous notifications remain disabled.

## Windows soak automation

The current-user task `Smartwatch Clank - Samsung Production Soak` is installed and enabled. It has twelve permanent daily triggers at even-hour `:30` times, giving a two-hour cadence without a hard-coded end date. It runs hidden and non-interactively while the user is logged on, resumes after reboot/login, starts when a scheduled time was missed, does not wake the PC, and ignores a new Task Scheduler instance while the previous one is active.

Task action:

```text
powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "<repo>\scripts\run_samsung_production_soak.ps1"
```

The wrapper sets `PYTHONPATH=<repo>\src`, uses `<repo>\.venv\Scripts\python.exe`, changes to the repository root, and invokes:

```text
python -m smartwatch_clank.soak_runner
```

The portable runner then invokes the unchanged canonical production path:

```text
python -m smartwatch_clank.cli run --mode production
```

Logs are daily UTF-8 files under `var/logs/soak`, retained for 30 days. Database history is never cleaned by the automation. Task Scheduler `IgnoreNew` and the existing atomic database lock jointly prevent overlap.

The scheduled action was manually triggered twice and returned Task Scheduler result `0`. A clean smoke cycle produced four healthy collectors, 394 reconciliation relationships, zero events, and exit code `0`; the final experimental database timestamp remained unchanged. The task's next scheduled execution after installation was 2026-08-09 16:30 local time.

## Portable continuation after the Windows host

Task Scheduler is a temporary launcher, not part of soak correctness. Core execution, locking, persistence, host identity, gap reporting, and log retention are cross-platform Python functionality. Every collector run now carries a common cycle ID and host metadata. A changed host identity is persisted as an explicit migration with the elapsed gap since the last recorded run.

Use `python -m smartwatch_clank.cli backup <output.sqlite3>` to create a consistent single-file transfer snapshot under the application lock. Restore that file as the canonical `var/smartwatch-clank.sqlite3` on the next host; do not copy a transient `.lock` file. The stored last-known-healthy observations and onboarding state resume directly, with no baseline reset. Time without execution remains visible through current/longest gaps and the migration record and is never backfilled.
