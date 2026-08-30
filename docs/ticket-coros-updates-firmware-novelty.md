# Ticket: coros_updates — false FIRMWARE_RELEASED novelty from article timestamps

Status: BLOCKED from production promotion (2026-08-30 final review). Planning artefact — no implementation yet.
Companion evidence: `docs/coros-updates-firmware-version-adjudication-2026-08-30.md`.

## Problem statement

`coros_updates` stores the Zendesk article `updated_at` editorial timestamp in the observation field
`firmware_version`. That field participates in the comparable observation state, so any Coros
help-centre article touch is diffed as a firmware change and classified `FIRMWARE_RELEASED`
(HIGH confidence, NEWSWORTHY). The source cannot safely run in production.

## Observed evidence

- 2026-08-28T18:43:09Z natural cycle persisted **23 simultaneous FIRMWARE_RELEASED events**, all HIGH,
  all carrying the identical current value `2026-08-28T17:53:02Z` — one site-wide Zendesk touch sweep.
- 27 FIRMWARE_RELEASED events total across the soak; **0 verifiable as actual firmware releases**.
- Two identities re-fired on adjacent cycles purely as their `updated_at` advanced (identity stable —
  the rediscoveries are legitimate; the *field semantics* are not).

## Root cause

An editorial timestamp is used as the change-detector state for a hardware-semantic event type.

## Exact affected code/config

- `src/smartwatch_clank/collectors/coros/updates.py` — writes `firmware_version = article/section.updated_at`.
- `src/smartwatch_clank/core/models.py` — `Observation.comparable()` includes `firmware_version` in diffed state.
- `src/smartwatch_clank/core/diff.py` — `_classify_change` (~line 34) maps a `firmware_version` delta
  directly to `FIRMWARE_RELEASED`.

## Minimum viable repair

Approach A (recommended, smallest): stop writing article `updated_at` into `firmware_version`
(leave the field absent/None). Article-level novelty remains identity-based: a genuinely new Coros
update article still surfaces as a new identity event; existing article identities with unchanged
identity keys produce no events.

Approach B (alternative, only if a real payload becomes parseable): keep a firmware-version field but
populate it exclusively from an actual parsed firmware-version payload, and gate `FIRMWARE_RELEASED`
on that payload's delta.

**Migration constraint (mandatory):** removing/blanking the field changes observation comparability —
on the first post-fix cycle every stored coros_updates observation would appear "changed" and could
emit a fresh burst. The implementation MUST use the repo's baseline re-pin/migration mechanism for
this collector so the schema change itself produces **zero events**, and MUST NOT rewrite or purge
stored soak history (evidence stays as-is; the gap is documented).

## Explicit non-goals

- No COROS collector-family rewrite (coros_official_news / coros_support untouched — the same
  `updated_at` family reaches them only as MONITOR-class `SPEC_CHANGED`, which is acceptable).
- No classification-ontology change; `_classify_change` semantics stay as-is.
- No delivery/notification work (source remains out of production until re-promoted).

## Tests required

1. Regression: a sweep that changes `updated_at` across N articles MUST NOT emit any
   `FIRMWARE_RELEASED` (and, post-migration, no events at all).
2. A genuinely new coros update article identity MUST still emit its identity-based event.
3. The baseline re-pin/migration cycle MUST produce zero events.
4. Existing coros collector suites stay green.

## Soak required after repair

Fresh soak clock at the first post-fix natural 6-hour cycle; **≥12 clean natural cycles**; zero mass
firmware bursts; manual adjudication of any real firmware event observed before re-review.

## Production exit condition

Re-review under the existing promotion policy after the soak above; both production gates
(tier + `production_allowlist`) applied per the standard per-source path.

## Rollback considerations

Single-commit revert restores prior behaviour; no DB migration is durable state (re-pin is a
documented one-time marker), so rollback is clean. Observations persisted post-fix keep the
new (absent-field) shape.

## Risk level

MEDIUM — the comparability migration is the only delicate part; everything else is a field removal.
