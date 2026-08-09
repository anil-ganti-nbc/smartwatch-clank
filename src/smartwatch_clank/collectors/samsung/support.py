from __future__ import annotations

import concurrent.futures
import hashlib
import json

from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation

from .common import HttpClient, SamsungRegion, UrlLibHttpClient, base_model, extract_model
from .parsing import family_name, page_title, parse_dimensions, support_urls


class SamsungSupportCollector(Collector):
    tier = CollectorTier.PRODUCTION

    def __init__(self, region: SamsungRegion, client: HttpClient | None = None, max_workers: int = 8) -> None:
        self.name = f"samsung_support_{region.code.lower()}"
        self.client = client or UrlLibHttpClient()
        self.region = region
        self.max_workers = max_workers

    def collect(self, context: CollectionContext) -> CollectorResult:
        observations: dict[str, Observation] = {}
        failures: list[dict[str, str]] = []
        sitemap_counts: dict[str, int] = {}
        candidates: list[tuple[SamsungRegion, str]] = []
        for region in (self.region,):
            sitemap = self.client.get_text(region.support_sitemap_url)
            urls = [url for url in support_urls(sitemap) if extract_model(url) and "/support/model" in url.lower()]
            urls = [url for url in urls if (extract_model(url) or "").startswith("SM-L")]
            sitemap_counts[region.code] = len(urls)
            candidates.extend((region, url) for url in urls)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.client.get_text, url): (region, url) for region, url in candidates}
            for future in concurrent.futures.as_completed(futures):
                region, url = futures[future]
                try:
                    page = future.result()
                except Exception as exc:
                    failures.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                model = extract_model(url)
                title = page_title(page)
                title_text = title or ""
                if "galaxy watch" in title_text.lower():
                    state, evidence = "known_smartwatch", ("official_support_sitemap", "explicit_galaxy_watch_title")
                elif "galaxy fit" in title_text.lower():
                    state, evidence = "non_smartwatch", ("official_support_sitemap", "fitness_band_title")
                else:
                    state, evidence = "ambiguous", ("official_support_sitemap", "sm_l_candidate", "no_explicit_watch_title")
                dimensions = parse_dimensions(title_text, url)
                identity = f"samsung:support:{region.code}:{model}"
                observations[identity] = Observation(
                    collector=self.name, identity=identity, source_url=url, observed_at=context.started_at,
                    source_kind="support", region=region.code, product_family=family_name(title_text),
                    family_identity=(family_name(title_text) or "").lower().replace(" ", "-") or None,
                    product_name=title if state == "known_smartwatch" else None, model_number=base_model(model),
                    regional_model_number=model, size=dimensions["size"], colour=dimensions["colour"],
                    connectivity=dimensions["connectivity"], classification_state=state,
                    classification_evidence=evidence, title=title,
                    page_hash=hashlib.sha256(json.dumps({
                        "model": model, "title": title, "classification": state, "evidence": evidence
                    }, sort_keys=True).encode()).hexdigest(),
                    support_metadata={"sitemap_url": region.support_sitemap_url, "candidate_rule": "SM-L + page classification"},
                    payload={"support_title": title},
                )
        if failures:
            raise RuntimeError(f"{len(failures)} Samsung support candidate pages failed; sample={failures[:3]}")
        return CollectorResult(tuple(observations[key] for key in sorted(observations)), {
            "sitemap_candidates": sitemap_counts, "parser_failures": failures
        })
