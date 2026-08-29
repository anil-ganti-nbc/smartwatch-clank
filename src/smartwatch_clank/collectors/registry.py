"""OEM registration list — the multi-OEM composition point.

Each entry is one manufacturer's registration function. Adding a new OEM
(Google, Garmin, Apple, ...) means adding one entry here, not restructuring
`default_registry()`. A failure while registering one OEM's collectors must
not prevent another OEM's collectors from registering (failure isolation
across OEMs).
"""

from __future__ import annotations

import os
from collections.abc import Callable

from smartwatch_clank.core.registry import CollectorRegistry

from .amazfit.catalogue import AmazfitCatalogueCollector
from .amazfit.official_news import AmazfitOfficialNewsCollector
from .apple.official_news import AppleOfficialNewsCollector
from .coros.official_news import CorosOfficialNewsCollector
from .coros.support import CorosSupportCollector
from .coros.updates import CorosUpdatesCollector
from .common import UrlLibHttpClient
from .garmin.catalogue import GarminCatalogueCollector
from .garmin.official_news import GarminOfficialNewsCollector
from .garmin.updates import GarminUpdatesCollector
from .google.official_news import GoogleOfficialNewsCollector
from .specialists.dcrainmaker import DCRainmakerSpecialistCollector
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
    # www.garmin.com sits behind a Cloudflare block that has been
    # Hetzner-hostile since Stage C (docs/stage-c-report.md, docs/hetzner-
    # deployment-2026-08-18.md) -- confirmed the same block from a fresh
    # Hetzner curl even with a browser UA, so it's IP-reputation, not a
    # header/UA fix. SMARTWATCH_CLANK_GARMIN_PROXY, when set, routes ONLY
    # these two www.garmin.com collectors through an HTTP-CONNECT egress
    # proxy (the NAS relay tunnel; see docs/garmin-egress-relay.md).
    # garmin_updates below is deliberately excluded: it hits
    # forums.garmin.com, a different subdomain that is NOT blocked, so
    # routing it through the relay would only add a dependency on the
    # tunnel for a collector that already works direct.
    garmin_proxy = os.environ.get("SMARTWATCH_CLANK_GARMIN_PROXY") or None
    registry.register(GarminOfficialNewsCollector(client=UrlLibHttpClient(proxy_url=garmin_proxy)))
    registry.register(GarminCatalogueCollector(client=UrlLibHttpClient(proxy_url=garmin_proxy)))
    # Wave 2 (2026-08-28): software-update intelligence via the beta-program
    # announcement RSS feeds - first-party, versioned, staff-authored.
    registry.register(GarminUpdatesCollector())


def _register_apple(registry: CollectorRegistry) -> None:
    registry.register(AppleOfficialNewsCollector())


def _register_specialists(registry: CollectorRegistry) -> None:
    # Wave 2 (2026-08-28): ONE specialist wearable-press source (DC Rainmaker)
    # -- distinct discovery value for leaks/certification finds that OEM
    # first-party surfaces miss; generic tech publications remain excluded.
    registry.register(DCRainmakerSpecialistCollector())


def _register_amazfit(registry: CollectorRegistry) -> None:
    registry.register(AmazfitOfficialNewsCollector())
    registry.register(AmazfitCatalogueCollector())


def _register_coros(registry: CollectorRegistry) -> None:
    registry.register(CorosSupportCollector())
    registry.register(CorosUpdatesCollector())
    registry.register(CorosOfficialNewsCollector())


# (oem, register) pairs. Order determines registration order only; the
# registry itself is order-independent (CollectorRegistry.all() sorts by
# name).
OEM_REGISTRATIONS: tuple[tuple[str, Callable[[CollectorRegistry], None]], ...] = (
    ("samsung", _register_samsung),
    ("google", _register_google),
    ("garmin", _register_garmin),
    ("apple", _register_apple),
    ("amazfit", _register_amazfit),
    ("coros", _register_coros),
    ("specialists", _register_specialists),
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
