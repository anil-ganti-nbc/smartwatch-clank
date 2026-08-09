# Samsung Stage 2 source research

Research date: 2026-08-09. Only public Samsung-controlled sources were used.

## Product catalogues

| Region | Official source | Result | Collector decision |
|---|---|---|---|
| US | `https://www.samsung.com/us/watches/all-watches/` | HTTP 200; product variants and full regional SKUs in JSON-LD | Use experimentally |
| UK | `https://www.samsung.com/uk/watches/all-watches/` | HTTP 200; product variants and full regional SKUs in JSON-LD | Use experimentally |
| India | `https://www.samsung.com/in/watches/all-watches/` | HTTP 200; product variants, price and availability in JSON-LD | Use experimentally |
| Germany | `https://www.samsung.com/de/watches/all-watches/` | HTTP 200; product variants and regional SKUs in JSON-LD | Use experimentally |
| South Korea | `https://www.samsung.com/sec/watches/all-watches/` | HTTP 200; materially different family-oriented links and no model codes in the initial HTML probe | Research/validation only until completeness is demonstrated |

Candidates come from each official catalogue's JSON-LD `ItemList`; no known-product list is consulted. Explicit Galaxy Watch names are accepted. Unknown families inside the watch category are retained as `probable_smartwatch`. Galaxy Fit, Ring, and accessory terms are rejected with evidence.

## Identity evidence

Observed Samsung URLs and structured items show that:

- Base hardware identifiers such as `SM-L340`, `SM-L345`, `SM-L350`, and `SM-L355` distinguish size and Bluetooth/LTE hardware.
- Full codes such as `SM-L340NZKAINS`, `SM-L340NZKAEUB`, and `SM-L340NZEAEUB` include colour/market packaging suffixes.
- Storefront URLs expose the full SKU while support titles often expose only the base hardware identifier.
- A stable observation therefore uses `region + full regional model/SKU`. It also retains the base model and a family relationship separately.
- URL fallback identity is allowed only when Samsung supplies no model code; it is visible as missing-model evidence and is not silently merged.

## Support infrastructure

The regional sitemap indexes link to independent support sitemaps in UK, India, and Germany. Live probes found tens of thousands of support model pages; a full crawl is not responsible for Stage 2. The bounded India experiment enumerates `SM-L` URLs from the official support sitemap, then fetches each page and requires an explicit Galaxy Watch title for `known_smartwatch`. A model pattern alone only produces `ambiguous`.

US returned 404 for `/us/support/sitemap.xml`. South Korea returned 403 for `/sec/support/sitemap.xml`. UK and Germany are enumerable but much larger than India, so they remain research-only for the initial support baseline.

Important limitation: filtering support candidates to `SM-L` can detect unknown variants in the currently evidenced Samsung watch namespace, but cannot prove discovery of a future family using a different prefix. The product category collector remains prefix-independent. No production promotion should occur until a broader, category-aware support enumeration mechanism is found.

## Firmware

Support pages expose manuals and downloads, but no bounded, clearly complete official firmware catalogue was established. Firmware remains research-only in Stage 2.

## Experimental live validation

Database: `var/experimental-samsung-stage2-final.sqlite3` (separate from the default state).

On 2026-08-09, a fresh baseline and an immediate repeat both completed with two healthy collectors. The baseline emitted zero discoveries; the unchanged repeat also emitted zero discoveries.

- Regions probed: US, UK, India, Germany, South Korea
- Structured product URLs: 54
- Rejected product entries: 3 (Galaxy Fit3 in UK, India, and Germany)
- Normalized watch candidates/variants: 51
- Product observations by region: US 14, UK 12, India 10, Germany 11, South Korea 4
- Classification: 47 known smartwatches, 4 probable smartwatches (Korean records without Latin model identifiers)
- Families: Galaxy Watch6 Classic, Watch7, Watch8, Watch8 Classic, Watch9, Watch Ultra, Watch Ultra2
- Full regional model codes: 44 unique; base model codes: 16 unique
- Connectivity: 23 Bluetooth/Wi-Fi, 25 LTE, 3 unknown
- India support entries: 57 (36 known smartwatches, 21 ambiguous, 0 non-watch)
- Support-only base model: `SM-L305`; support codes `SM-L305FZEAINS` and `SM-L305FZGAINS`, both titled Galaxy Watch7 LTE 4.0 cm
- Parser/network failures: 0
- Duplicate normalized identities: 0

The 21 ambiguous support entries expose only an identifier in the official title:

`SM-L300NZEASWA`, `SM-L300NZGASWA`, `SM-L310NZGASWA`, `SM-L310NZSASWA`,
`SM-L320NDAASWA`, `SM-L320NZSASWA`, `SM-L330NDAASWA`, `SM-L330NZSASWA`,
`SM-L340NZEAASA`, `SM-L340NZKAASA`, `SM-L350NZKAASA`, `SM-L350NZSAASA`,
`SM-L500NZKAASA`, `SM-L500NZKASWA`, `SM-L500NZWASWA`, `SM-L705FDAANPL`,
`SM-L705FZA1SLK`, `SM-L705FZS1SLK`, `SM-L705FZTANPL`, `SM-L705FZTASLK`,
and `SM-L705FZTASWA`.

## Production recommendation

**NO.** Product collection is repeatable across the tested snapshot, but catalogue completeness is not yet provable. South Korea lacks model identifiers in the observed catalogue data, support coverage is initially India-only, and the support enumeration is necessarily bounded to the evidenced `SM-L` namespace. Promotion requires repeated runs over time and resolution or explicit acceptance of these gaps.
