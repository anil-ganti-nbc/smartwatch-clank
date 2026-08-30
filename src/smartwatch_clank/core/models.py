from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CollectorTier(StrEnum):
    PRODUCTION = "production"
    EXPERIMENTAL = "experimental"


class RunScope(StrEnum):
    """Which collectors a run should select -- distinct from CollectorTier.

    A collector's `tier` is a property of the collector. `RunScope` is a
    property of one invocation. They deliberately don't mirror each other
    symmetrically:

    - PRODUCTION requires a collector to be BOTH `tier == PRODUCTION` AND
      named in the configured production allowlist -- two independent
      gates, both required.
    - EXPERIMENTAL selects `tier == EXPERIMENTAL` only, and never consults
      the production allowlist. A production-tier collector never runs
      under this scope, regardless of what the allowlist contains.
    - ALL is the explicit diagnostic/"everything" escape hatch. It replaces
      the old, accidental behavior where EXPERIMENTAL meant "every
      registered collector" -- that conflated "not production" with
      "run absolutely everything," which silently ran production-tier
      Samsung collectors through the experimental soak alongside the
      production cron.
    """

    PRODUCTION = "production"
    EXPERIMENTAL = "experimental"
    ALL = "all"


class SourceClass(StrEnum):
    """The lifecycle stage a piece of evidence came from.

    Values equal the free-text strings already stored in
    `Observation.source_kind` (`"product_catalogue"`, `"support"`) so this
    enum slots in without touching existing comparisons or stored JSON.
    """

    PRODUCT_CATALOGUE = "product_catalogue"
    SUPPORT = "support"
    OFFICIAL_NEWS = "official_news"
    SOFTWARE_UPDATE = "software_update"
    COMPANION_APP = "companion_app"
    CERTIFICATION = "certification"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EditorialLevel(StrEnum):
    CRITICAL = "1"
    NEWSWORTHY = "2"
    MONITOR = "3"
    NOISE = "4"


class ChangeType(StrEnum):
    NEW_DEVICE = "NEW_DEVICE"
    NEW_MODEL_NUMBER = "NEW_MODEL_NUMBER"
    NEW_REGIONAL_VARIANT = "NEW_REGIONAL_VARIANT"
    NEW_SIZE = "NEW_SIZE"
    NEW_COLOUR = "NEW_COLOUR"
    LTE_VARIANT = "LTE_VARIANT"
    PRODUCT_PAGE_APPEARED = "PRODUCT_PAGE_APPEARED"
    SUPPORT_PAGE_APPEARED = "SUPPORT_PAGE_APPEARED"
    CERTIFICATION_APPEARED = "CERTIFICATION_APPEARED"
    PRICE_CHANGE = "PRICE_CHANGE"
    AVAILABILITY_CHANGE = "AVAILABILITY_CHANGE"
    PREORDER_STARTED = "PREORDER_STARTED"
    SHIPPING_STARTED = "SHIPPING_STARTED"
    FIRMWARE_RELEASED = "FIRMWARE_RELEASED"
    OTA_RELEASED = "OTA_RELEASED"
    FEATURE_ADDED = "FEATURE_ADDED"
    FEATURE_REMOVED = "FEATURE_REMOVED"
    SPEC_CHANGED = "SPEC_CHANGED"
    PRODUCT_REMOVED = "PRODUCT_REMOVED"
    POSSIBLE_DISCONTINUATION = "POSSIBLE_DISCONTINUATION"
    SOURCE_LISTING_REMOVED = "SOURCE_LISTING_REMOVED"
    NEWS_ITEM_APPEARED = "NEWS_ITEM_APPEARED"


@dataclass(frozen=True, slots=True)
class Device:
    identity: str
    oem: str
    product_family: str | None = None
    product_name: str | None = None
    model_number: str | None = None
    regional_model_number: str | None = None
    size: str | None = None
    colour: str | None = None
    connectivity: str | None = None
    region: str | None = None
    source_url: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    status: str = "active"


@dataclass(frozen=True, slots=True)
class Observation:
    collector: str
    identity: str
    source_url: str
    observed_at: datetime = field(default_factory=utc_now)
    source_kind: str | None = None
    source_class: str | None = None
    oem: str | None = None
    region: str | None = None
    product_family: str | None = None
    family_identity: str | None = None
    product_name: str | None = None
    regional_model_number: str | None = None
    size: str | None = None
    colour: str | None = None
    connectivity: str | None = None
    classification_state: str | None = None
    classification_evidence: tuple[str, ...] = ()
    title: str | None = None
    model_number: str | None = None
    sku: str | None = None
    price: str | None = None
    currency: str | None = None
    availability: str | None = None
    specifications: dict[str, Any] = field(default_factory=dict)
    # Provenance-only field: comparable() diffs it and a delta classifies
    # FIRMWARE_RELEASED, so never populate it from publisher-maintained
    # timestamps (docs/ticket-coros-updates-firmware-novelty.md).
    firmware_version: str | None = None
    software_version: str | None = None
    support_metadata: dict[str, Any] = field(default_factory=dict)
    release_notes: str | None = None
    page_hash: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def comparable(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("observed_at")
        data.pop("collector")
        return data


@dataclass(frozen=True, slots=True)
class Discovery:
    collector: str
    identity: str
    change_type: ChangeType
    confidence: Confidence
    editorial_level: EditorialLevel
    source_url: str
    previous: dict[str, Any] | None
    current: dict[str, Any] | None
    evidence: dict[str, Any]
    discovered_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class CollectorResult:
    observations: tuple[Observation, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HealthRecord:
    collector: str
    healthy: bool
    observed_count: int
    previous_count: int | None
    warning: str | None = None
    error: str | None = None
    checked_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    collector: str
    healthy: bool
    baseline: bool
    observation_count: int
    discovery_count: int
    warning: str | None = None
    error: str | None = None
