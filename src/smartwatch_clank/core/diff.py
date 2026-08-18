from __future__ import annotations

from smartwatch_clank.classifiers.news import EDITORIAL_BY_CLASSIFICATION

from .models import ChangeType, Confidence, Discovery, EditorialLevel, Observation, SourceClass


def diff_catalogues(previous: dict[str, Observation], current: dict[str, Observation]) -> list[Discovery]:
    discoveries: list[Discovery] = []
    for identity in sorted(current.keys() - previous.keys()):
        item = current[identity]
        change = ChangeType.NEWS_ITEM_APPEARED if item.source_class == SourceClass.OFFICIAL_NEWS else ChangeType.NEW_DEVICE
        discoveries.append(_discovery(item, change, None, item.comparable()))
    for identity in sorted(previous.keys() & current.keys()):
        before, after = previous[identity], current[identity]
        if before.comparable() != after.comparable():
            discoveries.append(_discovery(after, _classify_change(before, after), before.comparable(), after.comparable()))
    for identity in sorted(previous.keys() - current.keys()):
        item = previous[identity]
        if item.source_class == SourceClass.OFFICIAL_NEWS:
            # A rolling RSS/Atom feed window losing an old article is expected
            # housekeeping, not a removed product or removed listing.
            continue
        change = ChangeType.SOURCE_LISTING_REMOVED if item.source_kind in {"product_catalogue", "support"} else ChangeType.PRODUCT_REMOVED
        discoveries.append(_discovery(item, change, item.comparable(), None))
    return discoveries


def _classify_change(before: Observation, after: Observation) -> ChangeType:
    if before.price != after.price or before.currency != after.currency:
        return ChangeType.PRICE_CHANGE
    if before.availability != after.availability:
        return ChangeType.AVAILABILITY_CHANGE
    if before.firmware_version != after.firmware_version:
        return ChangeType.FIRMWARE_RELEASED
    return ChangeType.SPEC_CHANGED


def _discovery(item: Observation, change: ChangeType, previous: dict | None, current: dict | None) -> Discovery:
    if change is ChangeType.NEWS_ITEM_APPEARED:
        confidence_value, level_value = EDITORIAL_BY_CLASSIFICATION.get(
            item.classification_state, ("LOW", EditorialLevel.NOISE.value)
        )
        confidence, level = Confidence(confidence_value), EditorialLevel(level_value)
    else:
        confidence = Confidence.HIGH
        level = EditorialLevel.NEWSWORTHY if change not in {ChangeType.SPEC_CHANGED, ChangeType.PRODUCT_REMOVED} else EditorialLevel.MONITOR
    return Discovery(
        collector=item.collector, identity=item.identity, change_type=change,
        confidence=confidence, editorial_level=level, source_url=item.source_url,
        previous=previous, current=current,
        evidence={"source_url": item.source_url, "change_type": change.value},
    )
