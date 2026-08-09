"""Collector implementations."""

from smartwatch_clank.core.registry import CollectorRegistry

from .samsung import SamsungProductCatalogueCollector, SamsungSupportCollector
from .samsung.common import SUPPORT_REGIONS


def default_registry() -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(SamsungProductCatalogueCollector())
    for region in SUPPORT_REGIONS:
        registry.register(SamsungSupportCollector(region))
    return registry
