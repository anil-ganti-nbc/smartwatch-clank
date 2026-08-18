"""Persist official-news classification results as generic cross-source evidence.

OEM-agnostic: works for any collector whose observations carry
`source_class == "official_news"` and a `classification_state`, regardless
of which OEM produced them. Reuses Stage A's `evidence_records`/
`evidence_timeline` tables (`core/store.py`) rather than inventing new
persistence.
"""

from __future__ import annotations

from typing import Any

from smartwatch_clank.classifiers.news import EDITORIAL_BY_CLASSIFICATION, NewsClassification
from smartwatch_clank.core.models import Observation
from smartwatch_clank.core.store import SQLiteStore

_PROMOTED_TO_EVIDENCE = {
    NewsClassification.SMARTWATCH_RELEVANT.value,
    NewsClassification.POSSIBLY_SMARTWATCH_RELEVANT.value,
}


def persist_news_evidence(store: SQLiteStore, observations: tuple[Observation, ...]) -> dict[str, Any]:
    counts = {"SMARTWATCH_RELEVANT": 0, "POSSIBLY_SMARTWATCH_RELEVANT": 0, "NOT_SMARTWATCH_RELEVANT": 0}
    evidence_written = 0
    for item in observations:
        state = item.classification_state or NewsClassification.NOT_SMARTWATCH_RELEVANT.value
        counts[state] = counts.get(state, 0) + 1
        if state not in _PROMOTED_TO_EVIDENCE:
            continue
        confidence, _ = EDITORIAL_BY_CLASSIFICATION.get(state, ("LOW", "4"))
        evidence_id = store.record_evidence(
            oem=item.oem or "unknown", source_class=item.source_class or "official_news", identity=item.identity,
            observed_at=item.observed_at, confidence=confidence, editorial_level=state, source_url=item.source_url,
            payload={"title": item.title, "classification_evidence": list(item.classification_evidence), **item.payload},
        )
        store.record_evidence_event(
            evidence_id=evidence_id, observed_at=item.observed_at, event="NEWS_CLASSIFIED",
            payload={"classification_state": state},
        )
        evidence_written += 1
    return {"processed": len(observations), "classification_counts": counts, "evidence_written": evidence_written}
