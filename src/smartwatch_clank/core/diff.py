from __future__ import annotations

from .models import ChangeType, Confidence, Discovery, EditorialLevel, Observation


def diff_catalogues(previous: dict[str, Observation], current: dict[str, Observation]) -> list[Discovery]:
    discoveries: list[Discovery] = []
    for identity in sorted(current.keys() - previous.keys()):
        item = current[identity]
        discoveries.append(_discovery(item, ChangeType.NEW_DEVICE, None, item.comparable()))
    for identity in sorted(previous.keys() & current.keys()):
        before, after = previous[identity], current[identity]
        if before.comparable() != after.comparable():
            discoveries.append(_discovery(after, _classify_change(before, after), before.comparable(), after.comparable()))
    for identity in sorted(previous.keys() - current.keys()):
        item = previous[identity]
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
    level = EditorialLevel.NEWSWORTHY if change not in {ChangeType.SPEC_CHANGED, ChangeType.PRODUCT_REMOVED} else EditorialLevel.MONITOR
    return Discovery(
        collector=item.collector, identity=item.identity, change_type=change,
        confidence=Confidence.HIGH, editorial_level=level, source_url=item.source_url,
        previous=previous, current=current,
        evidence={"source_url": item.source_url, "change_type": change.value},
    )
