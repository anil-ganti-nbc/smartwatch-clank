# Smartwatch Clank — Stage C: Garmin + Amazfit/Zepp + COROS Deep Coverage

Reprioritised Stage C: Garmin (deepest coverage), Amazfit/Zepp, COROS. Google/Pixel
Watch and the broader Wave B OEM expansion remain deferred per direct instruction.
Samsung production and the existing 4 official-news collectors are untouched
throughout. All new collectors are `CollectorTier.EXPERIMENTAL` and join the
Hetzner soak automatically via the already-corrected `RunScope.EXPERIMENTAL`
selection (see `docs/run-scope-correction-2026-08-19.md`) — no allowlist edit.

## A. Source research

All first-party sources investigated, with live evidence (curl/WebFetch against
the real sites, not assumptions):

| OEM | Source | Verdict |
|---|---|---|
| Garmin | catalogue | Built. `robots.txt` → `product-sitemap-index.xml` → per-locale `product-sitemap.xml`. en-US: 4,324 URLs across Garmin's *entire* catalogue (marine, aviation, cycling, accessories, watches — not watch-scoped). Product pages carry real server-rendered `Product` JSON-LD (2 `<script type="application/ld+json">` blocks each), but **no category/breadcrumb field at all** — confirmed on a real fenix 8 page (`/en-US/p/1228493/`). No watch-scoped index, search API, or Algolia hints found anywhere (`/en-US/search/`, homepage, category pages all checked). |
| Garmin | support | Not implemented. `support.garmin.com`'s only sitemap is `?faq=<opaque-id>` — ~1,000+ generic FAQ pages, no model/product scoping found. |
| Garmin | updates | Not implemented. `GarminDevice.xml`/WebUpdater is a device-side protocol requiring a physically connected unit — not a discovery source. No public firmware-changelog page/API found. |
| Garmin | official_news | Preserved as-is. `garmin.com/en-US/newsroom/feed/` confirmed still real RSS 2.0. Still 403s from Hetzner (Cloudflare bot-protection challenge, confirmed via curl with both the collector's UA and a browser UA) — no legitimate first-party alternative found (PR Newswire is third-party-hosted syndication, excluded per the "no competing/third-party publications" rule). |
| Amazfit/Zepp | catalogue | Built. `us.amazfit.com` is Shopify. `products.json` returns clean structured data (id, title, handle, price, tags, variants w/ SKU) — confirmed live. |
| Amazfit/Zepp | official_news | Built. `us.amazfit.com/blogs/news.atom` confirmed live Atom feed. |
| Amazfit/Zepp | support | Not implemented. `support.amazfit.com` exists but isn't Zendesk; its structure wasn't confirmed reliable within this pass. |
| Amazfit/Zepp | updates (Zepp OS) | Not implemented. No public firmware/changelog API found this pass; APK/app-metadata analysis is explicitly out of scope. |
| COROS | catalogue | Not implemented, architecturally rerouted. `coros.com` is a client-rendered Vue SPA; even `sitemap.xml` resolves to the SPA shell (not real XML) on direct fetch. `coros_support` (below) becomes COROS's primary discovery surface instead — the honest instantiation of "support-before-product" evidence, represented as `SourceClass.SUPPORT`, never forced into a fake catalogue. |
| COROS | support | Built. `support.coros.com` is a public **Zendesk Help Center with a JSON API** (`/api/v2/help_center/en-us/sections.json`, confirmed live, 92 sections). Per-model sections: "COROS PACE 4", "COROS APEX 4", "COROS APEX 2 Pro", "COROS VERTIX 2S", "COROS NOMAD", "COROS DURA", "COROS HYDROP", etc. |
| COROS | updates | Built, same API. A "Release Notes for COROS Devices" section (per-device changelogs) plus dated monthly "<Month> <Year> Feature Update" sections (fleet-wide rollouts). |
| COROS | official_news | Built, medium confidence. No RSS/JSON feed found for `coros.com/stories`; it's an HTML scrape of real server-rendered content — more fragile to markup changes than the RSS-based collectors. |

## B. Collectors implemented

| Name | Source class | Region/scope |
|---|---|---|
| `garmin_catalogue` | `product_catalogue` | Global (en-US sitemap) |
| `amazfit_catalogue` | `product_catalogue` | US storefront |
| `amazfit_official_news` | `official_news` | Global blog feed |
| `coros_support` | `support` | Global (en-us Help Center) |
| `coros_updates` | `software_update` | Global (en-us Help Center) |
| `coros_official_news` | `official_news` | Global stories listing |

All `CollectorTier.EXPERIMENTAL`. None added to `config/config.yaml`'s
`production_allowlist`.

## C. Garmin findings

Live baseline (real `product-sitemap.xml`, all 4,324 URLs, bounded 8-way
concurrency): see section H for exact final counts.

**Premium/specialist coverage**: MARQ, Tactix, Descent, D2, Quatix, Epix are all
in `KNOWN_WATCH_FAMILIES` as first-class evidence, not deprioritized for being
lower-volume — verified via a real MARQ Adventurer (Gen 2) fixture classifying as
`known_smartwatch` on the same footing as a Fenix.

**Real bugs found and fixed via live testing** (the exact reason a live baseline
run, not just fixture tests, was required before calling this "done"):
1. A single HTTP 404 on a stale/removed sitemap entry crashed the *entire*
   4,324-page crawl — `_fetch_one` only caught the typed `SourceHealthError`
   subclasses, not a bare `HTTPError`. Fixed: any per-page fetch exception is
   now isolated to that one product.
2. The initial "no signal → ambiguous, retained" fallback produced **over
   2,000** observations for products with zero watch-adjacent signal at all
   (e.g. "South America Coastal Charts", "GNC 300XL TSO", "GA 56 Low-Profile
   Antenna", "ActiveCaptain Business Program Plans") — Garmin's sitemap spans
   its entire business (marine, aviation, auto, eLearning), not just
   watches/accessories, so an unmatched item is overwhelmingly more likely to
   be unrelated than a new watch family. Fixed: the fallback is now rejection;
   new families still surface via the known-family or weak "watch"/
   "smartwatch" signal paths.
3. Garmin's real product names carry a registered-trademark symbol directly
   after the family name ("Approach® S70", "fēnix® 8"). The Approach
   sub-pattern's `\s*` didn't match `®`, so every real Approach product —
   watch and non-watch alike — fell through to "ambiguous" instead of being
   classified. Fixed: trademark symbols are stripped before matching.
4. An accessory named after its parent watch ("QuickFit 22 Watch Bands
   (Approach S60)") matched the Approach S-series pattern and was
   misclassified as the watch itself, because the Approach-specific branch
   ran before the generic accessory-keyword denylist. Fixed: accessory
   keywords are now checked first, universally, before any family-specific
   logic.

**Known limitation**: classification decisions are cached by product ID
(`var/garmin_catalogue_cache.json`, next to the database) so only new sitemap
entries get fetched after the first baseline crawl — but the cache stores the
*decision*, not the raw name/description, so a future change to
`classify_product()`'s logic will not retroactively reclassify already-cached
entries without deleting the cache file. Acceptable for an experimental
collector; noted for future hardening.

## D. Amazfit/Zepp findings

Live baseline (`products.json`, real): **21 accepted** (`known_smartwatch`),
**38 rejected** out of 59 total products. Zero `probable_smartwatch`/`ambiguous`
— Shopify's `product_type`/title data was clean enough that every real product
resolved confidently one way or the other.

Rejected correctly: bundle listings ("Amazfit T-Rex 3 + Helio Strap"), trade-in
program duplicates ("T-Rex 3 Trade-In"), gift cards, and non-watch wearables
that Shopify itself tags `product_type: "Smartwatch"` despite not being one
(Helio Strap/Strap Pro/Core — a chest-strap HR sensor; Band 7 — a fitness band;
Up Open-Ear Earbuds; Helio Ring). This is exactly the messiness the spec warned
about: Shopify's own category field is editorial metadata, not a strict
taxonomy, so classification is title-led with `product_type` as a secondary
signal, same design as Garmin.

News: live baseline showed 16 `SMARTWATCH_RELEVANT`, 8 `POSSIBLY`, 6
`NOT_RELEVANT` out of 30 processed — including a correct exclusion of "Introducing
the Helio Strap Pro" from smartwatch-relevant news.

**Software/ecosystem opportunity noted, not built**: Zepp OS firmware/changelog
infrastructure and companion-app metadata were flagged by the spec as
particularly interesting but no public API was confirmed reliable within this
research pass — reported honestly as not implemented rather than guessed at.

## E. COROS findings

Live baseline (`support.coros.com` Zendesk API, real):
- `coros_support`: **20 accepted** out of 92 sections — 15 real device models
  (APEX, APEX 2, APEX 2 Pro, APEX 4, APEX Pro, DURA, HYDROP, NOMAD, PACE 2,
  PACE 3, PACE 4, PACE Pro, VERTIX, VERTIX 2, VERTIX 2S) plus 5 `ambiguous`
  (2 "Firmware Updates" year-sections, 1 "June Firmware Update" — none matching
  the strict `<Month> <Year>` pattern so correctly retained rather than
  wrongly classified either way — and **2 real cross-brand devices**: "KIPRUN
  GPS 500"/"KIPRUN GPS 900", Decathlon's watch line built on COROS's own
  support infrastructure, discovered without any code change because they
  don't match the "COROS "-prefix pattern and were correctly retained as
  ambiguous rather than dropped).
- Two real accessories were correctly excluded from being treated as watches
  despite matching the exact same "COROS <name>" section-naming pattern:
  "COROS Heart Rate Monitor" (a chest strap) and "COROS POD 2" (a footpod
  sensor). A live run initially showed these leaking into `coros_updates` too
  (their release-notes articles use the identical title pattern) — fixed by
  applying the same accessory exclusion list there.

**Update infrastructure**: `coros_updates` found a "Release Notes for COROS
Devices" section with 17 real per-device articles (after excluding the 2
accessory ones) — including multi-device articles like "COROS APEX 4 (42) and
APEX 4 (46) Release Notes", correctly represented as one update event with an
`affected_devices` list (`["COROS APEX 4 (42)", "APEX 4 (46)"]`) rather than
two separate discoveries — plus 23 dated monthly feature-update sections
represented as fleet-wide-scoped events.

**Official news, medium confidence**: 15 stories parsed from a real
`coros.com/stories` fetch; found and fixed a title-quality bug where the
long-form scraped text ran on into a trailing publish-date/read-time suffix
("... 08/04/2026 3 min read") — now stripped.

## F. Identity model

Deliberately OEM-native, no fake universal identifier (per spec):

- **Garmin**: `garmin:catalogue:{sitemap_product_id}` — the numeric ID already
  in Garmin's own product URL path.
- **Amazfit**: `amazfit:catalogue:{shopify_product_id}` — Shopify's stable
  numeric `id`.
- **COROS**: `coros:support:{zendesk_section_id}` for device sections,
  `coros:update:{zendesk_article_id}` for per-device release notes,
  `coros:update:month:{zendesk_section_id}` for monthly fleet-wide updates,
  `coros:news:{sha1(href)[:16]}` for stories.

## G. Cross-source intelligence

- COROS demonstrates real source independence by construction: `coros_support`
  and `coros_updates` are two independently-timestamped views of the same
  Zendesk device sections, and neither depends on a COROS catalogue (which
  doesn't exist as a structured source).
- COROS's update evidence uses the "one event + affected-device relationships"
  shape from spec section 14 directly (via `payload["affected_devices"]`),
  not per-device discoveries — verified against the real
  "APEX 4 (42) and APEX 4 (46)" multi-device article.
- Editorial priority and evidence confidence remain the concepts they already
  were in Stage B (`classification_state`/`NewsClassification`'s existing
  confidence mapping) — no new field was introduced; premium Garmin families
  (MARQ/Tactix/Descent/D2/Quatix) are classified with the same confidence as
  any other known family, never auto-downgraded for niche positioning.

## H. Live baseline (final, post-fix)

**Garmin** (real `product-sitemap.xml`, all 4,324 URLs, fresh cache)
```
Catalogue candidates (sitemap URLs): 4,324
Accepted watches:     159 (156 known_smartwatch + 2 ambiguous + 1 probable_smartwatch)
Families (known_smartwatch, distinct name prefixes): fenix/fēnix, forerunner, venu,
  vivoactive, instinct, enduro, tactix, descent, quatix, epix, vivomove, marq,
  approach (S-series)
Variants: preserved per product page (size, price, currency; colour/material
  captured in payload description, not a separate structured field this stage)
Support observations: not implemented
Support-only identifiers: n/a
Software/update observations: not implemented
Official-news observations: 10 (0 relevant / 0 possibly / 10 not relevant, this snapshot)
Ambiguous: 2 (Approach R10/R50 -- real Garmin devices, golf launch monitors,
  matching neither the S-series watch nor G/Z/CT handheld pattern; correctly
  retained rather than guessed at)
Rejected: 3,192
Fetch failures (network/parse, not classification): 973 of 4,324 (see note below)
```
Note on the 973 fetch failures: not investigated exhaustively given this
stage's scope, but spot-checking showed a mix of true 404s (retired product
IDs still listed in the sitemap) and occasional timeouts under the crawl's
concurrency — none were 403s (Hetzner's Cloudflare-blocking pattern did not
reproduce locally for the catalogue crawl, only for the separate
`garmin_official_news` feed). All 973 are retried on the next run rather than
permanently cached, per the fetch-error-vs-classification-decision cache
design in section C.

**Amazfit/Zepp**
```
Catalogue candidates: 59
Accepted watches:     21
Rejected:             38
Support observations: not implemented
Software/update observations: not implemented
Official-news observations: 30 (16 relevant / 8 possibly / 6 not relevant)
Ambiguous: 0
Failures: 0
```

**COROS**
```
Support sections total: 92
Accepted devices: 20 (15 known + 5 ambiguous, incl. 2 real cross-brand Kiprun devices)
Rejected (topic/monthly/accessory sections): 72
Update events: 40 (17 per-device + 23 monthly fleet-wide)
Accessory update articles excluded: 2
Official-news observations: 15 (1 relevant / 0 possibly / 14 not relevant)
Failures: 0
```

## I. Ambiguous/rejected evidence, reported honestly

- Garmin: after both Approach regex fixes, only 2 products remain
  `ambiguous` — "Approach R10" and "Approach R50", real Garmin devices
  (golf launch monitors) that genuinely match neither the S-series watch
  pattern nor the G/Z/CT non-watch pattern. Retained, not dropped, per spec.
- COROS: "2022/2023 Firmware Updates" and "June Firmware Update" sections
  don't match the strict `<Month> <Year>` regex used to exclude monthly
  update sections from `coros_support` — correctly retained as `ambiguous`
  rather than silently misclassified as a device.

## J. Host compatibility (local vs. Hetzner)

| Collector | Local access | Hetzner access |
|---|---|---|
| `garmin_catalogue` | Confirmed working | **Blocked** — `SourceHostBlockedError: HTTP 403` on `product-sitemap.xml`. New finding: Garmin's Cloudflare block covers the whole `www.garmin.com` host from Hetzner's IP, not just `/newsroom/feed/`. Isolated cleanly — the other 9 collectors were unaffected. |
| `garmin_official_news` | Confirmed working | Confirmed **blocked** (Cloudflare 403) — pre-existing, unrelated to Stage C |
| `amazfit_catalogue` | Confirmed working | Confirmed working (21 observations, healthy) |
| `amazfit_official_news` | Confirmed working | Confirmed working (30 observations, healthy) |
| `coros_support` | Confirmed working | Confirmed working (20 observations, healthy) |
| `coros_updates` | Confirmed working | Confirmed working (40 observations, healthy) |
| `coros_official_news` | Confirmed working | Confirmed working (15 observations, healthy) |

## K. Tests

**163 passing locally** (up from 153 pre-Stage-C, 0 regressions). New tests by
area: Garmin catalogue (16, including 4 regression tests for the real bugs
found during live testing), Amazfit collectors (11), COROS collectors (19),
health taxonomy (6), cross-cutting multi-OEM registry (1 new).

## L. Git/GitHub

Branch: `expansion/stage-c-garmin-amazfit-coros`, commit `8bc79e4`. Merged to
`main` via [PR #15](https://github.com/anil-ganti-nbc/smartwatch-clank/pull/15)
(real merge commit, not squashed), merge SHA `d987b66ad3b6f96575ddf1c04f8a76833a837026`.
Local/GitHub SHA parity confirmed.

## M. Hetzner

Deployed via git (not SCP) to `/home/deploy/staging/smartwatch-clank`:
1. Backed up the pre-deploy database (`backups/smartwatch-clank-20260818T205037Z-pre-stage-c.sqlite3`, 512 runs / 51,699 observations) and verified it opens.
2. `git fetch` + `git checkout d987b66...` on the host.
3. Built `smartwatch-clank:d987b66`, tagged with the exact revision.
4. Three-way SHA verification: git HEAD, image `org.opencontainers.image.revision` label, and running `cli identity`'s `source_revision` all matched `d987b66ad3b6f96575ddf1c04f8a76833a837026`.
5. `.deployed-id` updated to `d987b66`.
6. Reinstalled the host-side test venv (`pip install -e .`) and ran the full suite: **163 passed**, matching local exactly.
7. Ran two manual experimental cycles through the real `deploy/run.sh` entrypoint: all 6 new Stage C collectors plus the 4 pre-existing experimental collectors ran; the 2 Garmin collectors were host-blocked (403, isolated cleanly); all others healthy, `baseline: true` on the first cycle and `baseline: false, discoveries: 0` on the second (no false spam between consecutive cycles).
8. Confirmed via direct SQL against the live volume: 532 total runs (up from 512 at backup time, +20 = 10 collectors × 2 manual cycles), 52,131 total observations, `samsung_product_catalogue` at 119 runs (was 118 at backup time — one normal production-cron cycle occurred during the deployment window, exactly as expected).
9. Production crontab entry confirmed byte-identical: `50 1-23/2 * * * /home/deploy/staging/smartwatch-clank/deploy_run.sh ...`.
10. `smartwatch-clank-soak.timer` confirmed still active/enabled, next fire unaffected by this deployment.

## N. Production isolation

Confirmed unchanged throughout: `config/config.yaml`'s `production_allowlist`
still lists exactly the 4 Samsung production collectors; no Samsung collector
code was touched; `samsung_official_news`/`google_official_news`/
`garmin_official_news`/`apple_official_news` all still pass their existing
tests unmodified in behavior (only `classifiers/news.py` gained new,
OEM-scoped phrase entries — additive, verified zero effect on the 4 existing
OEMs' classification test assertions).

## O. Preliminary collector-value ranking

1. **`coros_support`/`coros_updates`** — HIGH. Real, structured, versioned
   (Zendesk `updated_at`), and the strongest "support-before-product" signal
   of the three OEMs; already surfaced two real cross-brand devices (Kiprun)
   with zero code changes.
2. **`amazfit_catalogue`** — HIGH. Cleanest data of the three (Shopify JSON,
   zero ambiguous classifications), fast, cheap to run every cycle.
3. **`garmin_catalogue`** — MEDIUM-HIGH. Real signal (156 known watches
   found), but expensive (4,324-page crawl) and classification is
   name-pattern-based rather than category-based, so it's the most
   maintenance-prone of the three as Garmin's naming evolves.
4. **`amazfit_official_news`** — MEDIUM. Good relevant-story hit rate (16/30),
   standard RSS/Atom reliability.
5. **`coros_official_news`** — LOW-MEDIUM. Real content, but an HTML scrape
   (not a feed) and lower relevant-story hit rate (1/15) in this snapshot —
   flagged as the most fragile of the six new collectors.

Then STOP for human review. No Google/Apple/Huawei/Xiaomi/OnePlus/regulatory/
app-reverse-engineering work begins automatically.

---

# Wave 2 addendum (2026-08-28): recovery-first completion + Garmin software updates

This appendix documents the Wave 2 completion campaign against the Stage C
work above. Everything in sections A-O is preserved verbatim as history.

## Recovery matrix

| Prior asset | State | Wave 2 action |
|---|---|---|
| garmin_catalogue (4,324-URL sitemap crawl + classification + cache) | PRESENT, valid code | REUSED - no recrawl performed; live probe revalidated only |
| amazfit_catalogue (Shopify products.json) | PRESENT, live 2026-08-28 (60 products) | REUSED unchanged |
| amazfit_official_news (Atom feed) | PRESENT, live (30 entries) | REUSED unchanged |
| coros_support (Zendesk JSON) | PRESENT, live (sections endpoint healthy) | REUSED unchanged |
| coros_updates (per-device release notes; accessory filter fix intact) | PRESENT, live | REUSED unchanged |
| coros_official_news | PRESENT | REUSED unchanged |
| garmin_official_news (newsroom RSS) | PRESENT; Hetzner-hostile since Aug (Cloudflare 403) | Unchanged; not part of this wave's scope |
| Garmin software-update tracking | MISSING (Stage C: "not implemented") | **IMPLEMENTED** (`garmin_updates`) |
| Amazfit software-update tracking | MISSING | Investigated; honestly **deferred** (below) |
| Stage C crawl artefacts / classified-ID cache | Runtime-state only (var/), never committed by design | N/A - cache rebuilds on host |

## New in Wave 2

1. `garmin_updates` - Garmin software/firmware update intelligence. The
   surface Stage C missed: Garmin's own beta-program forums expose
   **official per-device-series announcement RSS feeds** whose items are
   staff-authored release-note posts ("Fenix 8/Quatix 8/Enduro 3/Fenix 8
   Pro/MicroLED version 23.27 - Available OTA"). Live probe: 5-family sweep,
   100 update observations with version + channel extracted from every one.
   Identity: link-hash based (`garmin:update:{sha1(link)[:16]}`), so a
   changelog edit inside an already-seen thread maps to the SAME identity -
   no duplicate NEW_UPDATE from title edits. Channel split:
   public_beta vs stable; affected families preserved as a list for
   multi-device titles. Feed failures are isolated per family (healthy
   families still record).
2. `dcrainmaker_specialist` - one specialist wearable-press source (brief cap
   2-3): distinct discovery value (launch leaks, certification finds, beta
   coverage that first-party surfaces miss); reuses OfficialNewsCollector +
   an additive specialist phrase set in classifiers/news.py scoped ONLY to
   this feed (zero effect on existing OEM classification assertions).

Not implemented, documented instead:

- **Amazfit software updates**: support.amazfit.com is a custom SPA; the
  Zendesk-style API path returns HTML (not JSON); no public changelog/feed
  was discoverable in bounded probes. Zepp OS versions appear only inside
  marketing copy on PDPs ("Zepp OS 4.5"), which cannot produce versioned
  per-release observations without building a fragile page-diff layer.
  Verdict: PROMISING_DEFER - re-check when Amazfit publishes structured
  release notes.
- Specialist #2/#3 held back deliberately: no other candidate demonstrated
  distinct discovery value over the combination of first-party feeds +
  DC Rainmaker (the5krunner reviewed: real RSS, but overlapping Garmin/COROS
  coverage; CORROBORATION_ONLY).

## Host-access expectations (deployment phase must verify)

| Collector | Local | Hetzner expectation |
|---|---|---|
| garmin_catalogue | ok | HETZNER_BLOCKED historically (403) - keep experimental |
| garmin_updates (new) | ok (RSS via forums.garmin.com - different subdomain than www) | UNTESTED_ON_HETZNER - verify; may survive where www.garmin.com is blocked |
| dcrainmaker_specialist (new) | ok | UNTESTED_ON_HETZNER |
| amazfit_* / coros_* | ok | Confirmed working during Stage C deployment |

## Tests

Full suite after Wave 2: **186 passed, 0 failed** (173 pre-Wave-2 baseline +
13 new across garmin_updates extraction/collection/isolation and specialist
registration/classification). Registry expectations extended additively;
no existing assertion weakened. Fleet Laws conformance suite: 10/10.
