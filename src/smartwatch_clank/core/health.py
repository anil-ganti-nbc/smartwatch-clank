from __future__ import annotations

from dataclasses import dataclass


class CatalogueHealthError(RuntimeError):
    pass


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

