"""OEM registration list — the multi-OEM composition point.

Each entry is one manufacturer's registration function. Adding a new OEM
(Google, Garmin, Apple, ...) means adding one entry here, not restructuring
`default_registry()`. A failure while registering one OEM's collectors must
not prevent another OEM's collectors from registering (failure isolation
across OEMs).
"""

from __future__ import annotations

from collections.abc import Callable

from smartwatch_clank.core.registry import CollectorRegistry

from .apple.official_news import AppleOfficialNewsCollector
from .garmin.official_news import GarminOfficialNewsCollector
from .google.official_news import GoogleOfficialNewsCollector
from .samsung import SamsungProductCatalogueCollector, SamsungSupportCollector
from .samsung.common import SUPPORT_REGIONS
from .samsung.official_news import SamsungOfficialNewsCollector


def _register_samsung(registry: CollectorRegistry) -> None:
    registry.register(SamsungProductCatalogueCollector())
    for region in SUPPORT_REGIONS:
        registry.register(SamsungSupportCollector(region))
    registry.register(SamsungOfficialNewsCollector())


def _register_google(registry: CollectorRegistry) -> None:
    registry.register(GoogleOfficialNewsCollector())


def _register_garmin(registry: CollectorRegistry) -> None:
    registry.register(GarminOfficialNewsCollector())


def _register_apple(registry: CollectorRegistry) -> None:
    registry.register(AppleOfficialNewsCollector())


# (oem, register) pairs. Order determines registration order only; the
# registry itself is order-independent (CollectorRegistry.all() sorts by
# name).
OEM_REGISTRATIONS: tuple[tuple[str, Callable[[CollectorRegistry], None]], ...] = (
    ("samsung", _register_samsung),
    ("google", _register_google),
    ("garmin", _register_garmin),
    ("apple", _register_apple),
)


def build_registry(
    registrations: tuple[tuple[str, Callable[[CollectorRegistry], None]], ...] = OEM_REGISTRATIONS,
) -> CollectorRegistry:
    """Build the registry, isolating one OEM's registration failure from the rest.

    A bug in a future OEM's registration function must not prevent an
    already-working OEM (e.g. Samsung) from registering. Failures are
    recorded on `registry.registration_failures` rather than raised, so
    callers can surface them without losing the collectors that did
    register successfully.
    """
    registry = CollectorRegistry()
    failures: dict[str, str] = {}
    for oem, register in registrations:
        try:
            register(registry)
        except Exception as exc:  # noqa: BLE001 - isolate one OEM's registration failure from the rest
            failures[oem] = f"{type(exc).__name__}: {exc}"
    registry.registration_failures = failures
    return registry
