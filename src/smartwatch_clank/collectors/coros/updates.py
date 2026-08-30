"""COROS software/firmware update intelligence, via the same Zendesk Help Center API.

Two real update surfaces exist in the live section list (see
docs/stage-c-report.md): a "Release Notes for COROS Devices" section whose
articles are per-device changelogs ("COROS PACE 4 Release Notes", "COROS
APEX 4 (42) and APEX 4 (46) Release Notes"), and dated monthly sections
("August 2026 Feature Update", "June 2026 Feature Update", ...) covering
fleet-wide feature rollouts. Per spec: one update should be one event with
affected-device relationships, not N separate discoveries -- so a per-device
release-notes article becomes one Observation with an `affected_devices`
list parsed from its title, and each monthly section becomes one
fleet-wide-scoped Observation, rather than fetching and diffing every
article inside every monthly section.

Invariant (2026-08-30 repair of the 2026-08-28 23-event false
FIRMWARE_RELEASED burst): the Zendesk `updated_at` editorial timestamp is
NEVER stored on the observation. `Observation.comparable()` diffs every
model field, so any publisher timestamp stored here would classify a
site-wide article touch as fleet-wide firmware releases. Article novelty
is identity-based only. No real firmware-version payload is exposed by
the section/article endpoints this collector reads (verdict C in
docs/ticket-coros-updates-firmware-novelty.md); if one ever is, real
firmware detection must be built on that parsed payload, not on
maintenance timestamps.
"""

from __future__ import annotations

import re

from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation, SourceClass

from ..common import HttpClient, UrlLibHttpClient
from .support import MONTH_UPDATE_RE, NON_DEVICE_COROS_SECTIONS, SECTIONS_URL

ARTICLES_URL_TEMPLATE = "https://support.coros.com/api/v2/help_center/en-us/sections/{section_id}/articles.json?per_page=100"
RELEASE_NOTES_SECTION_NAME = "Release Notes for COROS Devices"

_TITLE_SUFFIX_RE = re.compile(r"\s*Release Notes\s*$", re.I)


def parse_affected_devices(article_title: str) -> tuple[str, ...]:
    """"COROS APEX 4 (42) and APEX 4 (46) Release Notes" -> ("COROS APEX 4 (42)", "APEX 4 (46)")."""
    stripped = _TITLE_SUFFIX_RE.sub("", article_title).strip()
    parts = re.split(r"\s+and\s+|\s*,\s*", stripped)
    return tuple(part.strip() for part in parts if part.strip())


class CorosUpdatesCollector(Collector):
    name = "coros_updates"
    tier = CollectorTier.EXPERIMENTAL

    def __init__(self, client: HttpClient | None = None, sections_url: str = SECTIONS_URL) -> None:
        self.client = client or UrlLibHttpClient()
        self.sections_url = sections_url

    def collect(self, context: CollectionContext) -> CollectorResult:
        sections_data = self.client.get_json(self.sections_url)
        sections = sections_data.get("sections", [])
        observations: dict[str, Observation] = {}
        release_notes_section = next(
            (s for s in sections if (s.get("name") or "").strip().lower() == RELEASE_NOTES_SECTION_NAME.lower()), None
        )
        device_article_count = 0
        accessory_article_count = 0
        if release_notes_section is not None:
            articles_url = ARTICLES_URL_TEMPLATE.format(section_id=release_notes_section["id"])
            articles_data = self.client.get_json(articles_url)
            for article in articles_data.get("articles", []):
                title = article.get("title") or ""
                # The same "COROS <name> Release Notes" pattern covers real
                # watches AND accessories (confirmed live: "COROS Heart Rate
                # Monitor Release Notes", "COROS POD 2 Release Notes") --
                # exclude using the identical accessory list support.py uses,
                # per spec: accessories must never appear as watch updates.
                normalized_title = _TITLE_SUFFIX_RE.sub("", title).strip().lower()
                if normalized_title in NON_DEVICE_COROS_SECTIONS:
                    accessory_article_count += 1
                    continue
                device_article_count += 1
                identity = f"coros:update:{article.get('id')}"
                affected_devices = parse_affected_devices(title)
                observations[identity] = Observation(
                    collector=self.name, identity=identity, source_url=article.get("html_url") or articles_url,
                    observed_at=context.started_at, source_kind="software_update",
                    source_class=SourceClass.SOFTWARE_UPDATE.value, oem="coros",
                    title=article.get("title"),
                    payload={"affected_devices": list(affected_devices), "scope": "per_device"},
                )
        monthly_count = 0
        for section in sections:
            name = (section.get("name") or "").strip()
            if not MONTH_UPDATE_RE.match(name.lower()):
                continue
            monthly_count += 1
            identity = f"coros:update:month:{section.get('id')}"
            observations[identity] = Observation(
                collector=self.name, identity=identity, source_url=section.get("html_url") or self.sections_url,
                observed_at=context.started_at, source_kind="software_update",
                source_class=SourceClass.SOFTWARE_UPDATE.value, oem="coros",
                title=name,
                payload={"affected_devices": [], "scope": "fleet_wide"},
            )
        return CollectorResult(
            tuple(observations[key] for key in sorted(observations)),
            {
                "sections_url": self.sections_url, "release_notes_section_found": release_notes_section is not None,
                "per_device_articles": device_article_count, "accessory_articles_excluded": accessory_article_count,
                "monthly_sections": monthly_count,
            },
        )
