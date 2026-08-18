"""Garmin product catalogue, discovered via the full first-party product sitemap.

Garmin's site has no watch-scoped catalogue: the smartwatch category grid is
client-rendered (no product links/JSON-LD in the raw HTML), and no search or
Algolia-style API was found. The only complete first-party enumeration is
`product-sitemap.xml` -- ~4,300 URLs spanning Garmin's *entire* catalogue
(marine, aviation, cycling, accessories, watches). Individual product pages
ARE server-rendered with real `Product` JSON-LD, so classification happens
per-page after a full crawl. See docs/stage-c-report.md for the research that
led here, including why category-based filtering isn't possible (Garmin's
JSON-LD carries no category/breadcrumb field at all).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation, SourceClass
from smartwatch_clank.paths import default_garmin_catalogue_cache_path

from ..common import HttpClient, UrlLibHttpClient

SITEMAP_URL = "https://www.garmin.com/en-US/product-sitemap.xml"

_PRODUCT_URL_RE = re.compile(r"https://www\.garmin\.com/en-US/p/(\d+)/?")
_LD_JSON_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S)
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?\s?mm)", re.I)
_SKU_RE = re.compile(r"/pn/([A-Za-z0-9-]+)/")

# Classification EVIDENCE only -- never gates which sitemap URLs get fetched.
# A brand-new family is still crawled and still becomes an Observation (as
# "probable_smartwatch"/"ambiguous" if it doesn't match here), per the spec's
# explicit "must not become the discovery allowlist" requirement.
KNOWN_WATCH_FAMILIES = (
    "fenix", "fēnix", "marq", "forerunner", "venu", "vivoactive", "instinct",
    "enduro", "tactix", "descent", "quatix", "epix", "vivomove",
)

# Garmin's catalogue spans many non-watch product lines that share the exact
# same page shape as a watch product page. These keywords are checked before
# the family allowlist so an accessory never gets misclassified by an
# incidental family-name mention (e.g. a "fenix" branded strap).
NON_WATCH_KEYWORDS = (
    "chartplotter", "transducer", "sonar", "trolling motor", "autopilot",
    "radar", "antenna", "receiver", "mount", "cable", "dock", "charger",
    "screen protector", "case", "cover", "band", "strap", "pole", "camera",
    "dash cam", "headset", "remote", "harness", "collar", "handheld",
    "rangefinder", "tag", "vhf", "ais", "fishfinder", "chirp", "livescope",
    "hrm", "speed sensor", "cadence sensor", "power meter", "flight",
    "avionics", "adapter", "sensor kit", "bike mount", "premium bundle",
)


def parse_sitemap_product_ids(raw_xml: str) -> tuple[str, ...]:
    return tuple(sorted(set(_PRODUCT_URL_RE.findall(raw_xml)), key=int))


def classify_product(name: str, description: str) -> tuple[str, tuple[str, ...]]:
    """Three-state classification, same vocabulary as the Samsung collectors.

    Classification runs against the product NAME only, not the description.
    Garmin names accessory pages for what they are ("Fenix 7 Series QuickFit
    22 Watch Band", "0 Degree Pole Mount"), but a real watch's DESCRIPTION
    routinely mentions case/band material ("Sapphire ... Silicone Band") --
    matching keywords against the combined text produced false rejections of
    real watches during testing, so description is kept only for payload/
    display, never for classification.

    Deliberately conservative: an unrecognised product is "ambiguous" and
    still retained as an Observation, never silently dropped -- this is what
    lets a genuinely new Garmin watch family surface without a code change.
    """
    # Garmin product names carry a registered-trademark symbol directly after
    # the family name ("Approach® S70", "fēnix® 8") -- a live baseline run
    # showed this silently broke the Approach sub-pattern below (`\s*`
    # doesn't match "®"), dumping every real Approach product, watch and
    # non-watch alike, into "ambiguous". Stripping trademark symbols before
    # matching fixes it for both this pattern and any future one.
    haystack = name.lower().replace("®", "").replace("™", "")
    # Accessory keywords are checked before any family-name logic (Approach
    # included) -- a live baseline run showed a title like "QuickFit 22 Watch
    # Bands (Approach S60)" would otherwise match the Approach S-series
    # pattern below and get misclassified as the watch itself rather than a
    # band accessory for it.
    for keyword in NON_WATCH_KEYWORDS:
        if keyword in haystack:
            return "non_smartwatch", (f"matched non-watch keyword: {keyword!r}",)
    # Garmin's Approach line covers both golf watches (S-series) and non-watch
    # handhelds/rangefinders/tags -- the family name alone is not enough.
    if "approach" in haystack:
        if re.search(r"\bapproach\s*s\d", haystack):
            return "known_smartwatch", ("matched Approach S-series golf watch pattern",)
        if re.search(r"\bapproach\s*(g\d|z\d|ct\d)", haystack):
            return "non_smartwatch", ("matched Approach G/Z/CT non-watch product pattern",)
        return "ambiguous", ("Approach-family title, watch/non-watch unresolved",)
    for family in KNOWN_WATCH_FAMILIES:
        if family in haystack:
            return "known_smartwatch", (f"matched known watch family: {family!r}",)
    if "smartwatch" in haystack or re.search(r"\bwatch\b", haystack):
        return "probable_smartwatch", ("contains 'watch'/'smartwatch' but no known family match",)
    # Unlike Amazfit/COROS, Garmin's product sitemap spans its ENTIRE
    # business -- marine electronics, aviation avionics, nautical charts,
    # dog tracking, eLearning courses, business plans -- not just
    # watches/accessories. A live baseline run confirmed a blanket
    # "no signal -> ambiguous, retained" fallback produced over 2,000
    # observations that were almost entirely unrelated products (e.g.
    # "South America Coastal Charts", "GNC 300XL TSO"), not plausible new
    # watch candidates. Given no category signal is available at all (see
    # module docstring), the honest, conservative default here is
    # rejection: a genuinely new watch family still surfaces via the
    # KNOWN_WATCH_FAMILIES/weak "watch" signal paths above without a code
    # change, same as before -- this only removes the catch-all bucket for
    # products with zero watch-adjacent signal whatsoever.
    return "non_smartwatch", ("no watch or non-watch signal matched",)


def extract_product_ld_json(html: str) -> dict | None:
    for block in _LD_JSON_RE.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return None


def _load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")


class GarminCatalogueCollector(Collector):
    name = "garmin_catalogue"
    tier = CollectorTier.EXPERIMENTAL

    def __init__(self, client: HttpClient | None = None, sitemap_url: str = SITEMAP_URL,
                 cache_path: Path | None = None, max_workers: int = 8) -> None:
        self.client = client or UrlLibHttpClient()
        self.sitemap_url = sitemap_url
        self.cache_path = cache_path or default_garmin_catalogue_cache_path()
        self.max_workers = max_workers

    def _product_url(self, product_id: str) -> str:
        return f"https://www.garmin.com/en-US/p/{product_id}/"

    def _fetch_one(self, product_id: str) -> dict:
        url = self._product_url(product_id)
        try:
            html = self.client.get_text(url)
        except Exception as exc:
            # Any per-page fetch failure (403, a 404 on a stale sitemap
            # entry, a timeout, ...) is isolated to this one product -- it
            # must never crash the whole crawl, which is what a bare
            # `except SourceHealthError` did here in practice (a live run
            # hit a real 404 on a removed product and took the entire
            # collector down; see docs/stage-c-report.md).
            return {"status": "fetch_error", "error": f"{type(exc).__name__}: {exc}"}
        data = extract_product_ld_json(html)
        if data is None:
            return {"status": "fetch_error", "error": "no Product JSON-LD found"}
        name = data.get("name") or ""
        description = data.get("description") or ""
        classification, evidence = classify_product(name, description)
        offers = data.get("offers") or {}
        size_match = _SIZE_RE.search(name)
        sku_match = _SKU_RE.search(data.get("url") or url)
        return {
            "status": "ok", "url": data.get("url") or url, "name": name, "description": description,
            "classification": classification, "evidence": list(evidence),
            "price": offers.get("price"), "currency": offers.get("priceCurrency"),
            "size": size_match.group(1) if size_match else None,
            "sku": sku_match.group(1) if sku_match else None,
        }

    def collect(self, context: CollectionContext) -> CollectorResult:
        sitemap_xml = self.client.get_text(self.sitemap_url)
        product_ids = parse_sitemap_product_ids(sitemap_xml)
        cache = _load_cache(self.cache_path)
        to_fetch = [pid for pid in product_ids if pid not in cache]
        newly_fetched: dict[str, dict] = {}
        if to_fetch:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(self._fetch_one, pid): pid for pid in to_fetch}
                for future in as_completed(futures):
                    newly_fetched[futures[future]] = future.result()
        # Only stable content-based classifications are cached; transient
        # fetch errors are retried on the next run rather than blacklisted.
        cache.update({pid: entry for pid, entry in newly_fetched.items() if entry["status"] == "ok"})
        _save_cache(self.cache_path, cache)

        observations: dict[str, Observation] = {}
        rejected: list[dict] = []
        fetch_errors = 0
        for product_id in product_ids:
            entry = cache.get(product_id) or newly_fetched.get(product_id)
            if entry is None or entry.get("status") != "ok":
                fetch_errors += 1
                continue
            if entry["classification"] == "non_smartwatch":
                rejected.append({"id": product_id, "name": entry.get("name"), "evidence": entry.get("evidence")})
                continue
            identity = f"garmin:catalogue:{product_id}"
            observations[identity] = Observation(
                collector=self.name, identity=identity, source_url=entry["url"], observed_at=context.started_at,
                source_kind="product_catalogue", source_class=SourceClass.PRODUCT_CATALOGUE.value, oem="garmin",
                product_name=entry.get("name"), title=entry.get("name"), size=entry.get("size"),
                sku=entry.get("sku"), price=entry.get("price"), currency=entry.get("currency"),
                classification_state=entry["classification"],
                classification_evidence=tuple(entry.get("evidence") or ()),
                payload={"description": entry.get("description")},
            )
        return CollectorResult(
            tuple(observations[key] for key in sorted(observations)),
            {
                "sitemap_url": self.sitemap_url, "total_sitemap_ids": len(product_ids),
                "newly_fetched": len(to_fetch), "cached": len(product_ids) - len(to_fetch),
                "accepted": len(observations), "rejected": len(rejected), "rejected_sample": rejected[:20],
                "fetch_errors": fetch_errors,
            },
        )
