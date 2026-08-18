# Run-scope semantics correction — 2026-08-19

## The bug

`CollectorRegistry.selected(mode, production_allowlist)` treated
`mode is CollectorTier.EXPERIMENTAL` as "return every registered
collector" (`self.all()`), regardless of tier. This was inherited
unchanged from before the multi-OEM expansion, when there was only one
production-tier collector family and no experimental soak that ran
independently of production — so the distinction never mattered in
practice until Expansion Stage A added a second, independent experimental
soak timer on Hetzner.

Once that timer went live (`smartwatch-clank-soak.timer`, `--mode
experimental`), it silently re-ran the four production-tier Samsung
collectors (`samsung_product_catalogue`, `samsung_support_in`,
`samsung_support_gb`, `samsung_support_de`) on its own independent
2-hour cadence, in addition to their real production cron. Both schedules
wrote to the same database and were protected by the same `RunLock`, so
nothing corrupted or raced — but the Samsung collectors were running on
two schedules instead of one, which was never the intent.

## Old selection semantics

```python
def selected(self, mode: CollectorTier, production_allowlist=()):
    if mode is CollectorTier.EXPERIMENTAL:
        return self.all()                      # <- every collector, any tier
    allowed = set(production_allowlist)
    return tuple(c for c in self.all()
                 if c.tier is CollectorTier.PRODUCTION and c.name in allowed)
```

`CollectorTier` (a property of a *collector*) was being reused directly as
the run-mode argument (a property of one *invocation*), collapsing two
different concepts into one type and silently defining "experimental" as
"not explicitly production-gated" rather than "tier == experimental."

## New selection semantics

A new `RunScope` enum (`core/models.py`), independent of `CollectorTier`:

```python
def selected(self, scope: RunScope, production_allowlist=()):
    if scope is RunScope.ALL:
        return self.all()
    if scope is RunScope.EXPERIMENTAL:
        return tuple(c for c in self.all() if c.tier is CollectorTier.EXPERIMENTAL)
    allowed = set(production_allowlist)
    return tuple(c for c in self.all()
                 if c.tier is CollectorTier.PRODUCTION and c.name in allowed)
```

| Scope | Selection rule | Consults allowlist? |
|---|---|---|
| `PRODUCTION` | `tier == PRODUCTION` **and** name in allowlist | Yes (unchanged) |
| `EXPERIMENTAL` | `tier == EXPERIMENTAL` only | **No** (this is the fix) |
| `ALL` | every registered collector | No — explicit diagnostic escape hatch, replaces the old accidental "experimental means everything" behavior |

A future experimental collector joins the experimental soak automatically
by tier alone, with zero allowlist involvement. A future production-tier
collector never runs anywhere — production or experimental — until
explicitly allowlisted.

## CLI

The `--mode` flag name is unchanged (avoids touching the production cron's
already-baked-in `docker-compose.staging.yml` default command, the
Windows launcher scripts, and every existing test/doc that references
`--mode production`). Only the set of valid values and what they mean
changed:

```
smartwatch-clank run --mode production      # unchanged: tier==PRODUCTION AND allowlisted
smartwatch-clank run --mode experimental    # FIXED: tier==EXPERIMENTAL only, never allowlist-gated
smartwatch-clank run --mode all             # NEW: every registered collector (diagnostic)
```

`deploy/run.sh` (the experimental soak's launcher) already invoked
`run --mode experimental` — its invocation string needed **no change**;
only the meaning underneath it changed. Its stale comment (which
described the old "regardless of tier" behavior) was corrected, along
with `deploy/crontab.example`'s alternate-schedule example.

## Tests

New file `tests/test_run_scope_selection.py` — 10 tests, covering every
case requested plus one integration-level pair against the real deployed
registry (`collectors.default_registry()`, not just `DummyCollector`):

- production selects only allowlisted production collectors
- production-tier collector not in allowlist does not run
- experimental selects only experimental-tier collectors
- experimental excludes production-tier collectors **even if** they're
  passed in the allowlist argument (proves the allowlist has zero effect
  on experimental selection, not just that the default case works)
- `all` selects both tiers
- empty production allowlist selects zero production collectors
- a newly-registered experimental collector automatically joins the
  experimental soak with no allowlist edit
- a newly-registered production-tier collector joins neither production
  nor experimental without an explicit allowlist edit
- **(real registry)** `default_registry()`'s experimental selection is
  exactly the 4 official-news collectors, explicitly asserting none of
  the 4 real Samsung production collectors are present
- **(real registry)** `default_registry()`'s production selection under
  the real allowlist is exactly the 4 Samsung collectors, unchanged

Existing tests that relied on the old "`--mode experimental` = everything"
behavior as a convenience (mostly `Runner`-behavior tests using
`DummyCollector`, unrelated to scope semantics themselves) were migrated
to the new explicit `RunScope.ALL` diagnostic mode — 14 call sites across
`test_runner.py`, `test_samsung_collectors.py`, `test_run_provenance.py`,
`test_operations.py`; scope-semantics tests (`test_registry.py`,
`test_collector_registry_multi_oem.py`) were migrated to `RunScope.PRODUCTION`,
preserving their original intent exactly.

**Full suite: 109 passed, 0 failed** (99 before this fix + 10 new).

## Deployment

Same local → GitHub → Hetzner flow as every prior change this expansion.
`smartwatch-clank-soak.service`'s `ExecStart` (`deploy/run.sh`) needed no
edit — it already said `run --mode experimental`; only the application
code defining what that means was fixed and redeployed. The production
cron entry, its `:50`-past-odd-hours cadence, and `deploy_run.sh` (outside
version control) were **not touched**.

## Historical data

No runs were deleted or rewritten. The `runs` rows where Samsung
production collectors executed via the experimental soak (both during
this session's manual verification cycles and the one real timer-driven
cycle before this fix) remain exactly as recorded — genuine history of
what actually ran, not an error to be erased. From the deployment
revision where this fix went live onward, the experimental soak's
`runs` rows for those four collector names simply stop appearing, because
`RunScope.EXPERIMENTAL` no longer selects them — no explicit cutover
record was needed beyond that natural absence, readable directly from
`runs.started_at`/`runs.git_revision` per spec's evidence-timeline
principle (each source's own timestamps tell the story).

## Windows scheduler — explicitly unverified

The original Windows Task Scheduler production soak
(`docs/samsung-stage22-report.md`) is documented but its current state
cannot be checked remotely. This correction does not assume it is either
running or stopped, and nothing in this fix or in the Hetzner architecture
depends on it — Hetzner's production cron and experimental soak timer are
fully self-contained.
