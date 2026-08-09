from __future__ import annotations

import hashlib
import json

from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation

from .common import HttpClient, PRODUCT_REGIONS, SamsungRegion, UrlLibHttpClient
from .parsing import normalized_product, product_items


class SamsungProductCatalogueCollector(Collector):
    name = "samsung_product_catalogue"
    tier = CollectorTier.PRODUCTION

    def __init__(self, client: HttpClient | None = None, regions: tuple[SamsungRegion, ...] = PRODUCT_REGIONS) -> None:
        self.client = client or UrlLibHttpClient()
        self.regions = regions

    def collect(self, context: CollectionContext) -> CollectorResult:
        observations: dict[str, Observation] = {}
        rejected: list[dict] = []
        region_stats: dict[str, dict] = {}
        for region in self.regions:
            page = self.client.get_text(region.catalogue_url)
            items = product_items(page)
            accepted = 0
            for raw in items:
                item = normalized_product(raw, region.code)
                if item["classification"] == "non_smartwatch":
                    rejected.append(item)
                    continue
                accepted += 1
                fallback = hashlib.sha256(item["url"].encode()).hexdigest()[:16]
                variant_key = item["model"] or fallback
                identity = f"samsung:product:{region.code}:{variant_key}"
                observation = Observation(
                    collector=self.name, identity=identity, source_url=item["url"], observed_at=context.started_at,
                    source_kind="product_catalogue", region=region.code, product_family=item["family"],
                    family_identity=item["family_identity"], product_name=item["name"], model_number=item["base_model"],
                    regional_model_number=item["model"], size=item["size"], colour=item["colour"],
                    connectivity=item["connectivity"], classification_state=item["classification"],
                    classification_evidence=item["evidence"], title=item["name"], price=item["price"],
                    currency=item["currency"], availability=item["availability"],
                    page_hash=hashlib.sha256(json.dumps(item["raw"], sort_keys=True).encode()).hexdigest(),
                    payload={"source_item": item["raw"], "catalogue_url": region.catalogue_url},
                )
                observations.setdefault(identity, observation)
            region_stats[region.code] = {"structured_products": len(items), "accepted": accepted}
        return CollectorResult(tuple(observations[key] for key in sorted(observations)), {
            "regions": region_stats, "rejected": rejected, "duplicate_count": sum(v["accepted"] for v in region_stats.values()) - len(observations)
        })
