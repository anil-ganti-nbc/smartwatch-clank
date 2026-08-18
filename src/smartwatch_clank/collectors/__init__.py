"""Collector implementations."""

from smartwatch_clank.core.registry import CollectorRegistry

from .registry import build_registry


def default_registry() -> CollectorRegistry:
    return build_registry()
