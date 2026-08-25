# Source research — post-Stage-C reconnaissance ledger (2026-08-25)

Follows the Stage C report's explicit stop for further expansion decisions.
This ledger records the 2026-08-25 live recon pass over the remaining
declared Stage-C gaps. Decisions follow fleet source policy: official
structured surfaces first, no anti-bot engineering, one URL exposing an
already-covered catalogue is not a new source.

## Candidate records

### Garmin support / software-update surfaces (support.garmin.com, apps.garmin.com)
- class: OEM first-party (would be Class A)
- live check 2026-08-25: support.garmin.com returns 200 but is a JavaScript
  application with no stable public feed/API; apps.garmin.com endpoints return
  403 from this environment; buy.garmin.com returned 503.
- overlap: garmin_catalogue + garmin_official_news already cover catalogue and
  announcement signal.
- decision: **REJECT** — JS dependence + anti-bot behaviour without a simple
  public feed; bypass engineering is out of policy (campaign rule 10).
- note: the existing Hetzner Cloudflare block on www.garmin.com
  (docs/stage-c-report.md) remains an environment issue, not a parser issue.

### Amazfit support centre (support.amazfit.com)
- class: OEM first-party
- live check 2026-08-25: 200 but zero server-rendered content (huami CDN SPA,
  locale selector only). No extractable HTML contract.
- decision: **REJECT** — nothing to parse without headless-browser work;
  maintenance cost exceeds distinct signal.

### Zepp OS device library (docs.zepp.com)
- class: OEM first-party developer documentation
- live check 2026-08-25: docs v2.0 reorganization removed the former device
  library page (`/docs/reference/device-library/` -> 404); index exposes no
  replacement listing.
- decision: **DEFER** — no stable URL today. Revisit if Zepp Health restores a
  canonical device/OS-version table.

### Regional Amazfit catalogues (de/eu/in amazfit products.json)
- class: OEM first-party, but SAME underlying Shopify catalogue as the already-
  collected us.amazfit.com surface.
- decision: **REJECT** — not distinct signal per source policy.

### COROS product catalogue (coros.com)
- already resolved in Stage C: coros.com is a client-rendered SPA; the Zendesk
  Help Center API (support.coros.com/api/v2/...) was adopted as the primary
  discovery surface (coros_support + coros_updates).
- decision: **NO CHANGE** (existing coverage stands).

## Resulting posture

Source inventory remains 13 collectors across 6 OEMs:
Samsung x5 (production tier + allowlist), Garmin x2, Amazfit x2, COROS x3,
Google news, Apple news (experimental tier). No new collector passed the
live-recon bar on 2026-08-25; the next genuine gaps are environment-side
(Garmin IP block) rather than missing parsers.
