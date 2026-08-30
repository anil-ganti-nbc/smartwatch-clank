"""Garmin software-update intelligence via Garmin's own public beta-program
forum RSS (forums.garmin.com).

RECOVERY NOTE (Stage C follow-up): docs/stage-c-report.md recorded that
software-update tracking "was not implemented -- no public firmware-changelog
page/API found" during Stage C. Wave 2 re-verified this conclusion live and
found a first-party structured surface Stage C missed:

  forums.garmin.com beta-program per-device-series announcement forums expose
  official **RSS 2.0** feeds (".../f/announcements/rss?Threadless=1") whose
  items are Garmin-staff release-note posts carrying the version and channel
  directly in the title, e.g.

    "Fenix 8/Quatix 8/Enduro 3/Fenix 8 Pro/MicroLED version 23.27 - Available OTA"
    "Public Beta Version 18.23 - 100%"

These are the same posts Garmin staff publish for every stable OTA rollout
and public-beta release, they are version-dated (`pubDate`), and the feed is
hosted on Garmin's own forum infrastructure - first-party, unattended-
friendly, no browser automation, no credentials.

Identity rule (spec section 17): garmin:updates:{sha1(link)[:16]} keeps the
post link as evidence; the semantic identity is
(vendor=family-title-prefix, version, channel) extracted from the title.
Multi-family titles ("Fenix 8/Quatix 8/Enduro 3/...") are preserved verbatim
in payload["affected_families"] rather than split on guesses - one post is
one update observation with affected-family relationships, mirroring the
COROS updates design.

Channel extraction: "Public Beta"/"Beta" -> public_beta/beta; "- Available
OTA"/plain -> stable. Titles with no parseable version are retained but
flagged ambiguous in payload rather than guessed into a version.

Changelog-edit policy: identity is content-of-post-link based, so Garmin
editing changelog text inside an already-seen thread does NOT create a new
update observation; only a genuinely new post (new link) appears as new.
"""

from __future__ import annotations

import re

from smartwatch_clank.collectors.common import HttpClient, UrlLibHttpClient
from smartwatch_clank.collectors.feeds import parse_feed
from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation, SourceClass

FAMILY_FEEDS = {
    # forum slug fragment -> human-readable family label (evidence only)
    "fenix-8-series": "fenix-8",
    "forerunner-970": "forerunner-970",
    "forerunner-965": "forerunner-965",
    "forerunner-955": "forerunner-955",
    "venu-3-series": "venu-3",
    "epix-pro-gen-2": "epix-pro-gen-2",
    "instinct-3-series": "instinct-3",
    "enduro-3": "enduro-3",
}

FEED_URL_TEMPLATE = (
    "https://forums.garmin.com/beta-program/{family}/f/announcements/rss?Threadless=1"
)

_VERSION_RE = re.compile(r"\b(?:version\s*)?(\d{1,2}\.\d{2})\b", re.IGNORECASE)
_BETA_RE = re.compile(r"\b(beta)\b", re.IGNORECASE)
_OTA_RE = re.compile(r"-?\s*available\s+ota\b", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def extract_channel(title: str) -> str:
    if _BETA_RE.search(title):
        return "public_beta"
    return "stable"


def extract_version(title: str) -> str | None:
    m = _VERSION_RE.search(title)
    return m.group(1) if m else None


def extract_families(title: str) -> tuple[str, ...]:
    """Split multi-device titles on '/' while keeping tokens verbatim."""
    head = title.split(" version ")[0].split(" Beta ")[0]
    parts = [p.strip() for p in head.split("/") if p.strip()]
    return tuple(parts) if parts else (head.strip(),)


class GarminUpdatesCollector(Collector):
    name = "garmin_updates"
    tier = CollectorTier.PRODUCTION

    def __init__(self, client: HttpClient | None = None,
                 families: dict[str, str] | None = None) -> None:
        self.client = client or UrlLibHttpClient()
        self.families = families or FAMILY_FEEDS

    def collect(self, context: CollectionContext) -> CollectorResult:
        observations: dict[str, Observation] = {}
        per_family_counts: dict[str, int] = {}
        unversioned = 0
        for family_slug, family_label in sorted(self.families.items()):
            url = FEED_URL_TEMPLATE.format(family=family_slug)
            try:
                raw = self.client.get_text(url)
                if not raw:
                    raise RuntimeError(f"empty feed body for {url}")
                items = parse_feed(raw)
                if not items:
                    raise RuntimeError(f"feed returned zero parsable items for {url}")
            except Exception:  # noqa: BLE001 - one family failing must not kill the sweep
                per_family_counts[family_slug] = 0
                continue
            per_family_counts[family_slug] = len(items)
            for item in items:
                guid_source = item.guid or item.link
                identity = f"garmin:update:{hashlib_sha1(guid_source)}"
                version = extract_version(item.title)
                if version is None:
                    unversioned += 1
                observations.setdefault(identity, Observation(
                    collector=self.name,
                    identity=identity,
                    source_url=item.link,
                    observed_at=context.started_at,
                    source_kind="software_update",
                    source_class=SourceClass.SOFTWARE_UPDATE.value,
                    oem="garmin",
                    product_family=family_label,
                    title=_WHITESPACE_RE.sub(" ", item.title),
                    software_version=version,
                    classification_state=extract_channel(item.title),
                    classification_evidence=(
                        (f"OTA marker matched ({_OTA_RE.pattern})" if _OTA_RE.search(item.title)
                         else f"beta marker matched ({_BETA_RE.pattern})"
                         if _BETA_RE.search(item.title)
                         else "no explicit channel marker; defaulted to stable"),
                    ),
                    payload={
                        "affected_families": list(extract_families(item.title)),
                        "channel": extract_channel(item.title),
                        "feed_url": url,
                        "published_at": item.published_at,
                        "unversioned_title": version is None,
                    },
                ))
        return CollectorResult(
            tuple(observations[key] for key in sorted(observations)),
            {
                "feeds_requested": len(self.families),
                "per_family_item_counts": per_family_counts,
                "unversioned_titles": unversioned,
            },
        )


def hashlib_sha1(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode()).hexdigest()[:16]
