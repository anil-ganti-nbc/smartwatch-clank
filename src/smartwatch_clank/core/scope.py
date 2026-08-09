from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ScopeDecision(StrEnum):
    INCLUDE = "include"
    REVIEW = "review"
    EXCLUDE = "exclude"


@dataclass(frozen=True, slots=True)
class Scope:
    manufacturers: frozenset[str]
    included_device_types: frozenset[str]
    review_device_types: frozenset[str]
    excluded_device_types: frozenset[str]

    @classmethod
    def load(cls, path: Path) -> "Scope":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(*(frozenset(map(str.lower, data[key])) for key in (
            "manufacturers", "included_device_types", "review_device_types", "excluded_device_types"
        )))

    def decide(self, manufacturer: str, device_type: str) -> ScopeDecision:
        device_type = device_type.lower()
        if device_type in self.excluded_device_types:
            return ScopeDecision.EXCLUDE
        if manufacturer.lower() not in self.manufacturers or device_type in self.review_device_types:
            return ScopeDecision.REVIEW
        return ScopeDecision.INCLUDE if device_type in self.included_device_types else ScopeDecision.REVIEW

