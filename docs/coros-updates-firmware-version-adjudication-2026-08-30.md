# coros_updates `firmware_version` adjudication — 2026-08-30

Verdict: **C — capable of false firmware deltas. Promotion BLOCKED; stays EXPERIMENTAL.**

## The mapping

`CorosUpdatesCollector` (src/smartwatch_clank/collectors/coros/updates.py) maps the
Zendesk help-centre article/section `updated_at` timestamp into the Observation's
`firmware_version` field:

- per-device release-notes article: `firmware_version=article.get("updated_at")`
- monthly feature-update section: `firmware_version=section.get("updated_at")`

## Why the field is the change detector, not just payload

`Observation.comparable()` (src/smartwatch_clank/core/models.py) is the entire
observation minus `observed_at` and `collector` — so `firmware_version` is inside the
diffed state. `diff_catalogues` (src/smartwatch_clank/core/diff.py) emits a discovery
whenever `comparable()` changes, and `_classify_change` classifies a
`firmware_version` delta as `ChangeType.FIRMWARE_RELEASED` (HIGH confidence,
NEWSWORTHY editorial level), checked only after price/availability. A Zendesk
`updated_at` touch therefore produces a false "firmware released" event by
construction.

## Live evidence (experimental soak DB, 2026-08-27T02:10Z soak-clock start)

- 2026-08-28T18:43:09Z cycle: **23 simultaneous FIRMWARE_RELEASED discoveries, all
  HIGH confidence, all 23 carrying the identical current value
  `2026-08-28T17:53:02Z`** — a single site-wide article/section touch sweep across
  22 monthly-section identities and 1 per-device article, recorded as 23 "firmware
  releases" in one second of wall-clock diffing.
- Prior adjudication re-verified: rediscoveries are identity-stable field changes —
  e.g. `coros:update:43894707031060` re-fired 2026-08-29T00:43Z and 06:43Z purely as
  its `updated_at` advanced (2026-08-23T11:12:04Z → 00:12:23Z → 05:42:56Z), and
  `coros:update:20087973378068` fired 2026-08-28T12:43Z the same way.
- 27 FIRMWARE_RELEASED discoveries since soak start; 25 distinct identities; zero of
  them verifiable as an actual firmware release (no firmware-version payload exists
  in the Zendesk articles).

## Consequence

An article edit, typo fix, or help-centre sweep is indistinguishable from a firmware
release, at the highest editorial weight. Promoting coros_updates would bake a
false-event path into production; if delivery is ever enabled it would page on
editorial sweeps.

## Smallest next repair (not implemented here, per mission scope)

Decouple the change-detector from the editorial timestamp, either:

1. stop writing `updated_at` into `firmware_version` (leave `firmware_version=None`
   and drop `updated_at` from the observation entirely — new release-note articles
   still surface via identity-based NEW_DEVICE, and content edits stop producing any
   discovery), or
2. gate FIRMWARE_RELEASED on an actual parsed firmware-version payload once a
   structured version surface exists in the Zendesk articles.

Option 1 is the smaller change; a false delta then degrades to no event rather than
to a SPEC_CHANGED monitor note (any surviving copy of `updated_at` in the observation
still diffs via `comparable()`).

## Blocker text (registry wording)

BLOCKED: firmware_version is populated from the Zendesk article/section updated_at
editorial timestamp and that field is a change-detector in diff_catalogues, so any
site-wide article touch is recorded as N false HIGH/NEWSWORTHY FIRMWARE_RELEASED
events (observed live: 23 events at 2026-08-28T18:43:09Z sharing one timestamp).
Repair before promotion: decouple the change-detector from updated-at or gate on an
actual firmware-version payload.
