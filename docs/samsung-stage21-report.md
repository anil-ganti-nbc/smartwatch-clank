# Samsung Stage 2.1 hardening report

Research and validation date: 2026-08-09. All live evidence came from public Samsung-controlled sources.

## Source decisions

- India, UK, and Germany support now run as independent experimental collectors with separate baselines and health state.
- Their support sitemaps contain 57, 162, and 159 bounded `SM-L` candidates respectively.
- Sampled UK `SM-R` Watch, Buds, and Fit pages share the same embedded type/subtype codes (`19`/`1900`). The sitemap provides no title/category metadata. Broad `SM-R` enumeration therefore remains unsafe without fetching thousands of mixed records.
- Korean family pages expose model-specific buy/compare URLs, but each page also embeds a shared multi-generation comparison pool. Those identifiers are not used to fabricate family joins. Korean category records remain family-level with incomplete identifiers.
- Official mobile-product sitemaps contain many more historical/discoverable watch URLs than current `all-watches` JSON-LD. The category collector represents current merchandising, not a complete Samsung catalogue.

## Reconciliation

The independent reconciliation layer compares exact regional SKU, same-region base model, and global base model evidence. It persists:

- `CATALOGUE_AND_SUPPORT`
- `SUPPORT_ONLY_BASE_MODEL`
- `SUPPORT_ONLY_REGIONAL_SKU`
- `CATALOGUE_ONLY`
- `AMBIGUOUS_SUPPORT_ONLY`

Durable candidates preserve support first-seen, last-seen, classification evidence, matching catalogue evidence, catalogue-first-seen, candidate state, and source-onboarding baseline status. Newly enabled regions do not emit events. A later exact catalogue match emits `SUPPORT_BEFORE_PRODUCT` while retaining the original support timestamp.

Catalogue removals are represented as `SOURCE_LISTING_REMOVED`, not device discontinuations.

## Live validation

Dedicated final database: `var/experimental-samsung-stage21-final.sqlite3`.

Two final cycles followed three earlier diagnostic cycles. After excluding volatile review-count aggregates, both final cycles produced identical snapshots and zero discoveries/events.

- Product: US 14, UK 12, India 10, Germany 11, South Korea 4
- Support: India 57, UK 162, Germany 159
- Known smartwatch support observations: 138
- Ambiguous support observations: 240
- Unique base models across evidence: 17
- Regional SKU evidence identities: 393; unique literal SKU codes: 320
- Exact catalogue/support matches: 32
- Support-only base relationship records: 6, all base `SM-L305`
- Support-only regional SKU records for known bases: 100
- Catalogue-only regional/base records: 16
- Globally catalogue-only identified base: `SM-R960` (outside the bounded support namespace)
- Global-unknown confident candidates: 6 records, all `SM-L305`
- Rejected non-watch catalogue records: 3 Galaxy Fit3 records
- Duplicate identities: 0
- Parser failures: 0
- Source failures: 0

`SM-L305` remains absent from all five current category snapshots. Official support exposes two Galaxy Watch7 LTE 40 mm SKUs in each covered region:

- India: `SM-L305FZEAINS`, `SM-L305FZGAINS`
- UK: `SM-L305FZEAEUA`, `SM-L305FZGAEUA`
- Germany: `SM-L305FZEADBT`, `SM-L305FZGADBT`

## Commerce observations

All 51 product records supplied a price. Currencies matched region: USD 14, GBP 12, INR 10, EUR 11, KRW 4. Availability was 41 in-stock and 10 out-of-stock. JSON-LD supplied one price per listed record; it did not provide enough consistent semantics to distinguish list, sale, or configurator headline prices. Values are preserved, but editorial price alerting remains disabled.

## Production recommendations

- `samsung_product_catalogue`: **YES, WITH LIMITATIONS** — stable identities and repeatable results, but it is a merchandising snapshot, Korea lacks variant identifiers, and price semantics are limited.
- `samsung_support_in`: **YES, WITH LIMITATIONS** — stable and independently enumerable, but limited to safely bounded `SM-L` candidates.
- `samsung_support_gb`: **YES, WITH LIMITATIONS** — stable in repeated cycles, with the same `SM-L` namespace limitation and high ambiguous volume.
- `samsung_support_de`: **YES, WITH LIMITATIONS** — stable in repeated cycles, with the same `SM-L` namespace limitation and high ambiguous volume.

No collector has been promoted. Production allowlisting remains empty.
