"""Amazfit/Zepp product catalogue via the Shopify storefront `products.json` API.

`us.amazfit.com` is a Shopify store: `products.json` returns clean structured
data (confirmed live) -- but Shopify's `product_type`/`tags` are editorial
metadata set by Amazfit's storefront team, not a strict watch/non-watch
taxonomy. Real data showed `product_type == "Smartwatch"` covering actual
watches (Balance Ultra, T-Rex 3, Bip 6) *and* non-watch wearables (Helio
Strap, Band 7, Up Open-Ear Earbuds, Helio Ring), plus bundle listings
("T-Rex 3 + Helio Strap") and trade-in program duplicates with an empty
`product_type`. Classification here is title-led for the same reason as
Garmin's: Shopify's own category field isn't precise enough on its own.
"""

from __future__ import annotations

from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation, SourceClass

from ..common import HttpClient, UrlLibHttpClient

PRODUCTS_URL = "https://us.amazfit.com/products.json?limit=250"

# Classification EVIDENCE only, never a fetch-time gate -- every product in
# the storefront feed is inspected, so a new family surfaces as
# "probable_smartwatch" (via product_type) or "ambiguous", not silently
# dropped, per spec's "must not become the discovery allowlist" requirement.
KNOWN_WATCH_FAMILIES = ("balance", "bip", "cheetah", "active", "t-rex", "trex", "gtr", "gts", "falcon", "pop")

NON_WATCH_TITLE_KEYWORDS = (
    "helio strap", "helio core", "helio ring", "helio armband", "band 7", "up open-ear",
    "up earbuds", "earbuds", "armband", "arm sleeve", "charger", "charging accessor",
    "strap (", "straps (", " strap", "gift card",
)


def classify_product(title: str, product_type: str, tags: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    haystack = title.lower()
    if "+" in title:
        return "non_smartwatch", ("bundle listing (title contains '+')",)
    if "trade-in" in haystack:
        return "non_smartwatch", ("trade-in program duplicate listing",)
    if "gift card" in haystack:
        return "non_smartwatch", ("gift card, not a product",)
    if product_type.strip().lower() == "accessories":
        return "non_smartwatch", ("Shopify product_type=Accessories",)
    for keyword in NON_WATCH_TITLE_KEYWORDS:
        if keyword in haystack:
            return "non_smartwatch", (f"matched non-watch title keyword: {keyword!r}",)
    for family in KNOWN_WATCH_FAMILIES:
        if family in haystack:
            return "known_smartwatch", (f"matched known watch family: {family!r}",)
    if product_type.strip().lower() == "smartwatch":
        return "probable_smartwatch", ("Shopify product_type=Smartwatch but no known family match",)
    return "ambiguous", ("no watch or non-watch signal matched",)


class AmazfitCatalogueCollector(Collector):
    name = "amazfit_catalogue"
    tier = CollectorTier.EXPERIMENTAL

    def __init__(self, client: HttpClient | None = None, products_url: str = PRODUCTS_URL) -> None:
        self.client = client or UrlLibHttpClient()
        self.products_url = products_url

    def collect(self, context: CollectionContext) -> CollectorResult:
        data = self.client.get_json(self.products_url)
        products = data.get("products", [])
        observations: dict[str, Observation] = {}
        rejected: list[dict] = []
        for product in products:
            title = product.get("title") or ""
            product_type = product.get("product_type") or ""
            tags = tuple(product.get("tags") or ())
            classification, evidence = classify_product(title, product_type, tags)
            if classification == "non_smartwatch":
                rejected.append({"id": product.get("id"), "title": title, "evidence": list(evidence)})
                continue
            variants = product.get("variants") or []
            primary = variants[0] if variants else {}
            identity = f"amazfit:catalogue:{product.get('id')}"
            observations[identity] = Observation(
                collector=self.name, identity=identity,
                source_url=f"https://us.amazfit.com/products/{product.get('handle')}",
                observed_at=context.started_at, source_kind="product_catalogue",
                source_class=SourceClass.PRODUCT_CATALOGUE.value, oem="amazfit",
                product_name=title, title=title, sku=primary.get("sku"),
                colour=primary.get("option1") if primary.get("option1") != "Default Title" else None,
                size=primary.get("option2"), price=primary.get("price"), currency="USD",
                availability="available" if primary.get("available") else "unavailable",
                classification_state=classification, classification_evidence=evidence,
                payload={
                    "product_type": product_type, "tags": list(tags),
                    "variant_count": len(variants),
                    "variant_skus": [v.get("sku") for v in variants],
                },
            )
        return CollectorResult(
            tuple(observations[key] for key in sorted(observations)),
            {
                "products_url": self.products_url, "total_products": len(products),
                "accepted": len(observations), "rejected": len(rejected), "rejected_sample": rejected[:20],
            },
        )
