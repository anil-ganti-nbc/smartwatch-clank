"""Specialist collector tests (Wave 2): DC Rainmaker.

Verifies the reuse-based implementation (subclass of OfficialNewsCollector),
the specialist-scoped phrase classification (additive to classifiers/news.py
with zero effect on existing OEM assertions), registration, tier, and that a
fixture capture classifies sensibly. No network access.
"""

from __future__ import annotations

import json
from pathlib import Path

from smartwatch_clank.classifiers.news import classify_news
from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.specialists.dcrainmaker import (
    FEED_URL,
    DCRainmakerSpecialistCollector,
)
from smartwatch_clank.core.collector import CollectionContext
from smartwatch_clank.core.models import CollectorTier, RunScope

FIXTURES = Path(__file__).parent / "fixtures" / "specialists"


class TextFixtureClient:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def get_text(self, url: str) -> str:
        return self.responses[url]

    def get_json(self, url: str):
        raise NotImplementedError


def _collector(feed_text: str | None = None):
    text = feed_text if feed_text is not None else (
        FIXTURES / "dcrainmaker_feed.xml").read_text(encoding="utf-8")
    return DCRainmakerSpecialistCollector(client=TextFixtureClient({FEED_URL: text}))


def test_tier_and_name():
    c = _collector()
    assert c.tier == CollectorTier.EXPERIMENTAL
    assert c.name == "dcrainmaker_specialist"


def test_registration_in_default_registry():
    from smartwatch_clank.collectors import default_registry

    names = {c.name for c in default_registry().all()}
    assert "dcrainmaker_specialist" in names


def test_specialist_joins_experimental_scope_not_production():
    from smartwatch_clank.collectors import default_registry

    registry = default_registry()
    experimental = {c.name for c in registry.selected(RunScope.EXPERIMENTAL)}
    production = {c.name for c in registry.selected(
        RunScope.PRODUCTION,
        ("samsung_product_catalogue", "samsung_support_in",
         "samsung_support_gb", "samsung_support_de"),
    )}
    assert "dcrainmaker_specialist" in experimental
    assert "dcrainmaker_specialist" not in production


def test_fixture_classification_is_specialist_scoped():
    # Garmin-ecosystem post must hit via the specialist phrase set.
    cls, evidence = classify_news(
        "specialist_dcrainmaker",
        "Garmin Fenix 9 Early Leaks: What We Know",
        (), None)
    assert cls.value == "SMARTWATCH_RELEVANT"
    assert any("fenix" in e for e in evidence)


def test_non_wearable_post_stays_not_relevant():
    cls, _ = classify_news(
        "specialist_dcrainmaker",
        "Wahoo Kickr Bike Review", (), None)
    assert cls.value == "NOT_SMARTWATCH_RELEVANT"
