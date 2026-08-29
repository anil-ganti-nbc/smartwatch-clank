from __future__ import annotations

from dataclasses import dataclass


class CatalogueHealthError(RuntimeError):
    pass


class SourceHealthError(RuntimeError):
    """Base for typed source-fetch failures, distinct from parser/logic bugs.

    Lets a run's stored error say *why* a collector failed (host blocked us,
    we got rate limited, the response didn't parse) instead of a bare
    exception message -- see docs/hetzner-deployment-2026-08-18.md's Garmin
    403 case, which this taxonomy is built to represent going forward.
    """


class SourceHostBlockedError(SourceHealthError):
    """The source actively refused us (e.g. HTTP 403, bot-protection challenge)."""


class SourceRateLimitedError(SourceHealthError):
    """The source throttled us (e.g. HTTP 429)."""


class ParserFailureError(SourceHealthError):
    """The response was fetched successfully but couldn't be parsed as expected."""


class ProxyUnreachableError(SourceHealthError):
    """A configured egress proxy (e.g. the Garmin relay tunnel) could not be reached.

    Distinct from SourceHostBlockedError: the source itself was never contacted --
    the relay path is down. Kept separate so a dead tunnel is reported honestly
    rather than looking like the source blocked us again.
    """


@dataclass(frozen=True, slots=True)
class HealthAssessment:
    warning: str | None = None


def assess_catalogue(current_count: int, previous_count: int | None, *, unexpected_zero_is_failure: bool,
                     warning_ratio: float, failure_ratio: float) -> HealthAssessment:
    if current_count == 0 and unexpected_zero_is_failure:
        raise CatalogueHealthError("collector unexpectedly returned zero observations")
    if previous_count is None or previous_count == 0:
        return HealthAssessment()
    ratio = current_count / previous_count
    if ratio < failure_ratio:
        raise CatalogueHealthError(
            f"catalogue collapse: {current_count} observations vs {previous_count} previously ({ratio:.1%})"
        )
    if ratio < warning_ratio:
        return HealthAssessment(
            f"catalogue shrink warning: {current_count} observations vs {previous_count} previously ({ratio:.1%})"
        )
    return HealthAssessment()

