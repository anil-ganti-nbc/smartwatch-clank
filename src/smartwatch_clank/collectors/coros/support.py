"""COROS device support, via the public Zendesk Help Center JSON API.

`coros.com` itself is a client-rendered SPA with no first-party structured
catalogue (even `sitemap.xml` resolves to the SPA shell, not real XML -- see
docs/stage-c-report.md). `support.coros.com` is a separate, public Zendesk
Help Center with a real JSON API (`/api/v2/help_center/en-us/sections.json`,
confirmed live, 92 sections). Its per-model sections ("COROS PACE 4", "COROS
APEX 2 Pro", ...) become COROS's PRIMARY discovery surface -- this is the
honest instantiation of the spec's "support-before-product" evidence
concept, not a workaround: COROS device identity genuinely *is* its support
section here, represented as `SourceClass.SUPPORT`, never forced into a fake
catalogue.
"""

from __future__ import annotations

import re

from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation, SourceClass

from ..common import HttpClient, UrlLibHttpClient

SECTIONS_URL = "https://support.coros.com/api/v2/help_center/en-us/sections.json?per_page=100"

# Sections that match the "COROS <name>" naming pattern but are known NOT to
# be a watch (an accessory/sensor sold under the same naming convention, or
# an account/app topic page rather than a device page). Confirmed against
# the real live section list.
NON_DEVICE_COROS_SECTIONS = {
    "coros account", "coros app", "coros heart rate monitor", "coros pod 2", "coros pod",
}

# Generic support-topic sections confirmed against the real live section
# list -- not device-specific, so excluded rather than treated as ambiguous.
KNOWN_TOPIC_SECTIONS = {
    "navigation", "troubleshoot", "warranty", "accessories", "activity page",
    "battery & solar charging", "battery performance", "beta testing",
    "bike speed and cadence sensors", "climbing", "connectivity & pairing",
    "cycling & swimming", "cycling fitness metrics", "daily use", "data syncing",
    "dura updates", "fishing", "flexibility / mobility", "gps, hr & other sensors",
    "getting started", "how-to videos", "language & orientation", "maintenance",
    "profile page", "quick start guide", "receive help from coros",
    "recreational & court sports", "running & hiking", "running fitness metrics",
    "safety features", "screen visibility", "shipping, returns & refunds",
    "status updates", "strength, cardio, multisport & triathlon", "training hub",
    "training with dura", "watch face icons & widgets", "watch menu overview",
    "water sports", "winter sports", "workouts", "release notes for coros devices",
    "release notes for coros app",
}

MONTH_UPDATE_RE = re.compile(
    r"^(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}",
    re.I,
)


def classify_section(name: str) -> tuple[str, tuple[str, ...]]:
    """Three-state classification, same vocabulary as the other OEM collectors.

    A "COROS <model>" section is the strong positive signal. Anything else is
    either a known generic topic/monthly-update section (excluded) or an
    unrecognised name -- kept as "ambiguous" rather than dropped, since a
    genuine cross-brand device family (e.g. Decathlon's Kiprun line, built on
    COROS's own support infrastructure -- confirmed present in the real
    section list as "KIPRUN GPS 500"/"KIPRUN GPS 900") would otherwise never
    surface without a code change.
    """
    lower = name.strip().lower()
    if lower in NON_DEVICE_COROS_SECTIONS:
        return "non_smartwatch", (f"known non-device COROS section: {name!r}",)
    if lower.startswith("coros "):
        if "release notes" in lower or "update" in lower:
            return "non_smartwatch", ("release-notes/updates section, not a device",)
        return "known_smartwatch", ("matched 'COROS <model>' section naming pattern",)
    if MONTH_UPDATE_RE.match(lower) or lower in KNOWN_TOPIC_SECTIONS:
        return "non_smartwatch", ("generic support-topic or monthly-update section, not a device",)
    return "ambiguous", ("non-'COROS'-prefixed section name, device status unresolved",)


class CorosSupportCollector(Collector):
    name = "coros_support"
    tier = CollectorTier.PRODUCTION

    def __init__(self, client: HttpClient | None = None, sections_url: str = SECTIONS_URL) -> None:
        self.client = client or UrlLibHttpClient()
        self.sections_url = sections_url

    def collect(self, context: CollectionContext) -> CollectorResult:
        data = self.client.get_json(self.sections_url)
        sections = data.get("sections", [])
        observations: dict[str, Observation] = {}
        rejected: list[dict] = []
        for section in sections:
            name = section.get("name") or ""
            classification, evidence = classify_section(name)
            if classification == "non_smartwatch":
                rejected.append({"id": section.get("id"), "name": name, "evidence": list(evidence)})
                continue
            identity = f"coros:support:{section.get('id')}"
            observations[identity] = Observation(
                collector=self.name, identity=identity, source_url=section.get("html_url") or self.sections_url,
                observed_at=context.started_at, source_kind="support", source_class=SourceClass.SUPPORT.value,
                oem="coros", product_name=name, title=name,
                classification_state=classification, classification_evidence=evidence,
                payload={"zendesk_section_id": section.get("id"), "updated_at": section.get("updated_at")},
            )
        return CollectorResult(
            tuple(observations[key] for key in sorted(observations)),
            {
                "sections_url": self.sections_url, "total_sections": len(sections),
                "accepted": len(observations), "rejected": len(rejected), "rejected_sample": rejected[:20],
            },
        )
