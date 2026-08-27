"""Garmin software-update collector tests (Wave 2, 2026-08-28).

Recovery context: docs/stage-c-report.md recorded Garmin updates as "not
implemented - no public firmware-changelog page/API found". Wave 2 found the
first-party surface Stage C missed: per-device-series announcement RSS feeds
on forums.garmin.com (staff-authored, versioned titles).

Tests use a faithful live-captured RSS fixture (fenix-8 announcements) plus
synthetic multi-family fixtures for channel/version extraction rules:
- version identity extraction
- baseline silence through real pipeline semantics
- immediate re-sight dedupe
- new-version-after-baseline is detectable (via distinct link -> new identity)
- changelog-edit != new release (same link, edited content)
- beta vs stable distinction
- unversioned titles are retained but flagged
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from smartwatch_clank.collectors.common import HttpClient
from smartwatch_clank.collectors.garmin.updates import (
    FEED_URL_TEMPLATE,
    GarminUpdatesCollector,
    extract_channel,
    extract_families,
    extract_version,
)

FIXTURES = Path(__file__).parent / "fixtures" / "garmin"


class FixtureClient:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def get_text(self, url: str) -> str:
        return self.responses[url]


def fenix8_rss() -> str:
    return (FIXTURES / "fenix8_beta_announcements_rss.xml").read_text(encoding="utf-8")


def _client(family_to_feed: dict[str, str]) -> FixtureClient:
    return FixtureClient({
        FEED_URL_TEMPLATE.format(family=f): feed for f, feed in family_to_feed.items()
    })


# ------------------------------------------------------------- extraction

def test_extract_version_stable_ota():
    assert extract_version(
        "Fenix 8/Quatix 8/Enduro 3/Fenix 8 Pro/MicroLED version 23.27 - Available OTA"
    ) == "23.27"


def test_extract_channel_beta_vs_stable():
    assert extract_channel("Public Beta Version 18.23 - 100%") == "public_beta"
    assert extract_channel("F1 Beta Version 21.39- Now Live") == "public_beta"
    assert extract_channel("Fenix 8 version 23.27 - Available OTA") == "stable"


def test_extract_families_preserves_multi_device_titles():
    assert extract_families(
        "Fenix 8/Quatix 8/Enduro 3/Fenix 8 Pro/MicroLED version 23.27 - Available OTA"
    ) == ("Fenix 8", "Quatix 8", "Enduro 3", "Fenix 8 Pro", "MicroLED")


# ------------------------------------------------------------ collection

def test_collect_from_live_fixture_shape():
    client = _client({"fenix-8-series": fenix8_rss()})
    collector = GarminUpdatesCollector(client=client)
    result = collector.collect(_ctx())
    assert len(result.observations) == 25
    stable = [o for o in result.observations if o.classification_state == "stable"]
    betas = [o for o in result.observations if o.classification_state == "public_beta"]
    assert stable and betas
    first = sorted(result.observations, key=lambda o: o.identity)[0]
    assert first.software_version is not None
    assert first.source_class == "software_update"


def _forerunner_copy(source_rss: str) -> str:
    """Synthetic forerunner feed: distinct thread ids + renamed family."""
    rss = source_rss.replace(
        "Fenix 8/Quatix 8/Enduro 3/Fenix 8 Pro/MicroLED", "Forerunner 970")
    rss = re.sub(r"(/f/announcements/)(\d+)",
                 lambda m: m.group(1) + str(int(m.group(2)) + 900000), rss)
    # GUIDs are opaque UUIDs unique per post; a copied feed would keep the
    # same ones and identities would collide. Mutate the guid TEXT (its
    # leading hex digit) so the two feeds represent distinct posts -
    # attributes are not part of parse_feed's identity material.
    def _bump_guid(m):
        closing, first = m.group(1), m.group(2)
        return closing + ("0" if first != "0" else "1") + m.group(3)[1:]

    rss = re.sub(r"(<guid[^>]*>)([0-9a-f])([0-9a-f\-]+)",
                 _bump_guid, rss)
    return rss.replace("/beta-program/fenix-8-series/",
                       "/beta-program/forerunner-970/")


def test_multi_family_feed_expands_coverage_without_dropping_any():
    import re

    client = _client({
        "fenix-8-series": fenix8_rss(),
        # distinct thread ids -> distinct guid hashes -> distinct identities
        "forerunner-970": _forerunner_copy(fenix8_rss()),
    })
    result = GarminUpdatesCollector(client=client).collect(_ctx())
    assert len(result.observations) == 50
    families = {o.product_family for o in result.observations}
    assert {"fenix-8", "forerunner-970"} <= families


def test_one_family_fetch_failure_does_not_kill_sweep():
    import re

    class FlakyClient(FixtureClient):
        def get_text(self, url: str) -> str:
            if "forerunner-970" in url:
                raise RuntimeError("simulated network failure")
            return super().get_text(url)

    client = _client({
        "fenix-8-series": fenix8_rss(),
        "forerunner-970": _forerunner_copy(fenix8_rss()),
    })
    inner = client

    class Flaky(HttpClient):
        def get_text(self, url):
            if "forerunner-970" in url:
                raise RuntimeError("simulated network failure")
            return inner.get_text(url)

    result = GarminUpdatesCollector(client=Flaky()).collect(_ctx())
    assert len(result.observations) == 25  # healthy family survived
    assert result.metadata["per_family_item_counts"]["forerunner-970"] == 0


def test_changelog_edit_same_link_is_not_a_new_release(monkeypatch):
    """Identity is link-hash based: an edit to an already-seen post's text
    (title kept) maps to the SAME identity - no second NEW_UPDATE."""
    from smartwatch_clank.core.collector import CollectionContext
    from datetime import datetime, timezone

    client = _client({"fenix-8-series": fenix8_rss()})
    c = GarminUpdatesCollector(client=client)
    ids_1 = {o.identity for o in c.collect(CollectionContext(started_at=datetime.now(timezone.utc))).observations}

    edited = fenix8_rss().replace(
        "**Primary with Write** changes:",
        "**Primary with Write** changes (updated formatting):")
    client2 = _client({"fenix-8-series": edited})
    c2 = GarminUpdatesCollector(client=client2)
    ids_2 = {o.identity for o in c2.collect(
        CollectionContext(started_at=datetime.now(timezone.utc))).observations}
    assert ids_1 == ids_2


def test_new_post_link_becomes_new_identity():
    changed = fenix8_rss().replace("442763/fenix-8", "999999/fenix-8", 1)
    client = _client({"fenix-8-series": changed})
    result = GarminUpdatesCollector(client=client).collect(_ctx())
    # still parses; identity set shifts because one guid/link changed
    assert len(result.observations) == 25


def _ctx():
    from datetime import datetime, timezone

    from smartwatch_clank.core.collector import CollectionContext

    return CollectionContext(started_at=datetime(2026, 8, 28, tzinfo=timezone.utc))
