# Smartwatch Clank — Expansion Stage B report

## Scope

Official-news collectors for Samsung, Google, Garmin, and Apple Newsroom
(spec section 26, Stage B), built on top of Expansion Stage A's shared
infrastructure. All four collectors are `EXPERIMENTAL` — the production
allowlist (still exactly the 4 Samsung collectors) is unchanged.

## Sources researched

Live-fetched during this session (not simulated), each already returning
real content:

| OEM | Feed | Format | Real signal confirmed |
|---|---|---|---|
| Samsung | `https://news.samsung.com/global/feed` | RSS 2.0 | `<category>` tags include exact `Galaxy Watch`/`Galaxy Watch9`/`Galaxy Watch Ultra2` |
| Google | `https://blog.google/products/pixel/rss/` (redirects to `blog.google/products-and-platforms/devices/pixel/rss/`; `urllib` follows this automatically) | RSS 2.0 | Titles explicitly say "Pixel Watch 5: ..." (category alone is generic `Pixel`, not watch-specific — title matching was necessary) |
| Garmin | `https://www.garmin.com/en-US/newsroom/feed/` | RSS 2.0 | Category `Wearables / Health`; **the real feed contains "CIRQA Smart Band"** (must exclude) and **"Approach Z10" — a laser rangefinder, not a watch** (confirms the design decision not to auto-treat "Approach" as a relevant phrase) |
| Apple | `https://www.apple.com/newsroom/rss-feed.rss` | Atom 1.0 | Valid, parses correctly; **no Apple Watch items in the live feed at fetch time** — expected per spec section 9 (Apple Newsroom is one of the least useful pre-announcement sources) |

No general web search was used as a collector, per spec section 5.

## Collectors created (4, all EXPERIMENTAL)

`samsung_official_news`, `google_official_news`, `garmin_official_news`,
`apple_official_news` — all built on one shared
`collectors/news_collector.py::OfficialNewsCollector` base (fetch -> parse
-> classify -> `Observation`), since all four real feeds needed identical
handling. New shared, OEM-agnostic infra: `collectors/common.py` (generic
`HttpClient`; kept independent from `collectors/samsung/common.py`'s
existing one — see "notable design decision" below), `collectors/feeds.py`
(stdlib RSS 2.0 + Atom 1.0 parser), `classifiers/news.py` (deterministic
`classify_news`), `intelligence/news.py` (`persist_news_evidence`, the
first real consumer of Stage A's `evidence_records`/`evidence_timeline`
tables).

## Exact live baseline counts (this session, real feeds, scratch database)

| Collector | Observations | SMARTWATCH_RELEVANT | POSSIBLY | NOT_RELEVANT | Evidence written |
|---|---:|---:|---:|---:|---:|
| samsung_official_news | 50 | 11 | 0 | 39 | 11 |
| google_official_news | 20 | 4 | 0 | 16 | 4 |
| garmin_official_news | 10 | 0 | 0 | 10 | 0 |
| apple_official_news | 20 | 0 | 0 | 20 | 0 |

Garmin and Apple both currently have zero smartwatch-relevant items live —
genuinely quiet news windows for both, not a classifier defect (confirmed
by inspecting the raw feed content directly). A second live cycle a few
minutes later produced identical item sets on all four feeds (0 new
discoveries, 0 rolled-off items) — expected given the short interval.
`evidence_records.first_seen` was verified to survive that second cycle
unchanged while `last_seen` advanced, confirming Stage A's "never overwrite
first_seen" guarantee holds under this stage's first real write traffic.
Samsung's own product/support reconciliation was unaffected: still 394
relationships, byte-identical in shape to the Stage A baseline.

## Classification design and limitations

Deterministic rule cascade (relevant phrase > excluded phrase > weak
signal "watch"/"wearable" > not relevant) — no ML/fuzzy scoring, matching
spec section 6's "do not alert merely because it contains the word watch."
Known limitations, to revisit if real soak data warrants it:
- Garmin's `Approach` family is deliberately excluded from the relevant-phrase
  list rather than downgraded to POSSIBLY, because the real feed shows
  Approach also covers non-watch handheld golf GPS units. An actual
  Approach *watch* announcement would currently classify NOT_RELEVANT
  unless it also contains "watch"/"wearable" text (in which case it would
  land as POSSIBLY). Worth revisiting once a real Approach watch article
  is observed.
- Classification only inspects title + categories + a short summary/
  description (truncated ~500 chars) — never full article bodies, both for
  storage sanity and copyright hygiene.

## Diff/discovery behavior (new, additive, source-class-aware)

Two small conditional branches were added to `core/diff.py`, both proven
against the real 8-collector run above (0 spurious discoveries on either
baseline or repeat cycle):
1. A brand-new news identity produces `ChangeType.NEWS_ITEM_APPEARED`
   (not `NEW_DEVICE`), with `Discovery.confidence`/`editorial_level`
   derived from `classification_state` rather than the blanket
   NEWSWORTHY rule used for catalogue/support changes.
2. An article rolling off the RSS/Atom feed window produces no `Discovery`
   at all for `source_class == "official_news"` — existing catalogue/
   support removal semantics (`SOURCE_LISTING_REMOVED`/`PRODUCT_REMOVED`)
   are unchanged for every other source.

## Tests

- Before Stage B: 72 tests passing.
- After Stage B: **99 tests passing, 0 regressions** (27 new: 5 in
  `test_feeds.py`, 9 in `test_news_classification.py`, 5 in
  `test_official_news_collectors.py`, 5 in `test_diff_news.py`, 3 in
  `test_news_evidence.py`).
- Fixtures (`tests/fixtures/news/*.xml`) mix real trimmed feed content
  (Samsung's Galaxy Watch9 launch item, Garmin's real CIRQA Smart Band and
  Approach Z10 items, Google's real Pixel Watch 5 item, Apple's real
  Manufacturing Center/earnings items) with a small number of clearly-
  necessary constructed cases (an Apple Watch announcement, since none was
  live at fetch time; a couple of bare-"watch"-word ambiguous headlines)
  to exercise every classification branch deterministically.

## Database/schema changes

**None.** Stage A's `evidence_records`/`evidence_timeline` tables and
`runs` provenance columns are reused as-is. `ChangeType.NEWS_ITEM_APPEARED`
is a new `StrEnum` member stored as `TEXT` in the existing `discoveries`
table — no `ALTER TABLE` needed. Schema version stays `2`.

## Notable design decision: HTTP client duplication

`collectors/common.py`'s `HttpClient`/`UrlLibHttpClient` duplicates
`collectors/samsung/common.py`'s implementation (~20 lines) rather than
extracting a shared one. Reason: `tests/test_http_retry.py` patches
`smartwatch_clank.collectors.samsung.common.time.sleep`/`...urlopen`
directly, and those patch targets only work if the class stays defined in
that exact module. Moving it would break a working, currently-passing test
for no benefit. Documented in the module docstring so it isn't mistaken
for an oversight later.

## Soak participation

Still local-only — no collector has been deployed to Hetzner. This
session's live runs were entirely against scratch databases (deleted
afterward); the real `var/smartwatch-clank.sqlite3` was never touched, and
the live Hetzner Samsung soak is completely unaffected by this branch.

## Deployment status

```
Local:
    branch:  expansion/stage-b-official-news (based on
             expansion/stage-a-shared-infrastructure, itself not yet
             merged to main)
    tests:   99 passed, 0 failed
    working tree: clean after commit

GitHub:
    pushed commit: (see below, pushed after this report)

Hetzner:
    deployed commit: unchanged (still the prior production revision;
                      Stage A was also not deployed)

Parity:
    local/GitHub/Hetzner source revisions match: NO (by design) --
    two feature branches (Stage A, Stage B) are now stacked ahead of
    main and Hetzner. Both still need PRs opened/merged before the
    deferred Hetzner deployment follow-up.
```

## Recommendation

The official-news pattern (shared HTTP client, shared feed parser, shared
collector base, deterministic classifier, generic evidence dispatch) held
up cleanly across four genuinely different real feeds without per-OEM
special-casing beyond a name/URL. No architectural problems surfaced.

Two things worth doing before piling on more collectors:
1. **Open PRs for Stage A and Stage B and get them merged to `main`** —
   two stacked, unmerged feature branches is starting to accumulate risk;
   merging now keeps future stages simple.
2. Either **Expansion Stage C** (Google catalogue/support collectors,
   researched fresh rather than cloned from Samsung, per spec section 10)
   or the **deferred Hetzner deployment** (`scripts/deploy_hetzner.sh`) —
   either is safe to do first.
