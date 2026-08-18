"""Shared official-news collector logic.

Every OEM's newsroom feed needs the same fetch -> parse -> classify ->
Observation pipeline (confirmed identical across all four real feeds
researched for Stage B: Samsung, Google, Garmin, Apple). Per-OEM modules
just supply the OEM name, feed URL, and collector name.
"""

from __future__ import annotations

import hashlib

from smartwatch_clank.classifiers.news import classify_news
from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation, SourceClass

from .common import HttpClient, UrlLibHttpClient
from .feeds import parse_feed


class OfficialNewsCollector(Collector):
    tier = CollectorTier.EXPERIMENTAL

    def __init__(self, *, oem: str, feed_url: str, name: str, client: HttpClient | None = None) -> None:
        self.oem = oem
        self.feed_url = feed_url
        self.name = name
        self.client = client or UrlLibHttpClient()

    def collect(self, context: CollectionContext) -> CollectorResult:
        raw = self.client.get_text(self.feed_url)
        items = parse_feed(raw)
        observations: dict[str, Observation] = {}
        classification_counts = {"SMARTWATCH_RELEVANT": 0, "POSSIBLY_SMARTWATCH_RELEVANT": 0, "NOT_SMARTWATCH_RELEVANT": 0}
        for item in items:
            classification, evidence = classify_news(self.oem, item.title, item.categories, item.summary)
            classification_counts[classification.value] += 1
            guid_source = item.guid or item.link
            identity = f"{self.oem}:news:{hashlib.sha1(guid_source.encode()).hexdigest()[:16]}"
            observations.setdefault(identity, Observation(
                collector=self.name, identity=identity, source_url=item.link, observed_at=context.started_at,
                source_kind="official_news", source_class=SourceClass.OFFICIAL_NEWS.value, oem=self.oem,
                title=item.title, classification_state=classification.value, classification_evidence=evidence,
                payload={
                    "categories": list(item.categories), "published_at": item.published_at,
                    "summary": item.summary,
                },
            ))
        return CollectorResult(
            tuple(observations[key] for key in sorted(observations)),
            {"feed_url": self.feed_url, "classification_counts": classification_counts},
        )
