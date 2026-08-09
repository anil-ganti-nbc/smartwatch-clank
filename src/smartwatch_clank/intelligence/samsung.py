from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum

from smartwatch_clank.core.models import Observation, utc_now


class SamsungRelationship(StrEnum):
    CATALOGUE_AND_SUPPORT = "CATALOGUE_AND_SUPPORT"
    SUPPORT_ONLY_BASE_MODEL = "SUPPORT_ONLY_BASE_MODEL"
    SUPPORT_ONLY_REGIONAL_SKU = "SUPPORT_ONLY_REGIONAL_SKU"
    CATALOGUE_ONLY = "CATALOGUE_ONLY"
    AMBIGUOUS_SUPPORT_ONLY = "AMBIGUOUS_SUPPORT_ONLY"


class CandidateState(StrEnum):
    GLOBAL_UNKNOWN_SUPPORT_MODEL = "GLOBAL_UNKNOWN_SUPPORT_MODEL"
    KNOWN_GLOBALLY_SUPPORT_FIRST_IN_REGION = "KNOWN_GLOBALLY_SUPPORT_FIRST_IN_REGION"
    AMBIGUOUS_OFFICIAL_IDENTIFIER = "AMBIGUOUS_OFFICIAL_IDENTIFIER"
    COMMERCIALLY_CONFIRMED = "COMMERCIALLY_CONFIRMED"


@dataclass(frozen=True, slots=True)
class SamsungReconciliation:
    relationship: SamsungRelationship
    region: str
    base_model: str | None
    regional_sku: str | None
    product_identity: str | None
    support_identity: str | None
    support_url: str | None
    support_observed_at: datetime | None
    classification_evidence: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.relationship}:{self.region}:{self.regional_sku or self.base_model or self.product_identity}"


def reconcile_samsung(product_observations: tuple[Observation, ...], support_observations: tuple[Observation, ...]) -> tuple[SamsungReconciliation, ...]:
    products = tuple(sorted(product_observations, key=lambda item: item.identity))
    supports = tuple(sorted(support_observations, key=lambda item: item.identity))
    product_exact = {(item.region, item.regional_model_number): item for item in products if item.regional_model_number}
    product_global_bases = {item.model_number for item in products if item.model_number}
    support_regional_bases = {(item.region, item.model_number) for item in supports
                              if item.model_number and item.classification_state == "known_smartwatch"}
    records: list[SamsungReconciliation] = []

    for item in supports:
        exact = product_exact.get((item.region, item.regional_model_number))
        if item.classification_state != "known_smartwatch":
            relationship = SamsungRelationship.AMBIGUOUS_SUPPORT_ONLY
        elif exact:
            relationship = SamsungRelationship.CATALOGUE_AND_SUPPORT
        elif item.model_number not in product_global_bases:
            relationship = SamsungRelationship.SUPPORT_ONLY_BASE_MODEL
        else:
            relationship = SamsungRelationship.SUPPORT_ONLY_REGIONAL_SKU
        records.append(SamsungReconciliation(
            relationship, item.region or "UNKNOWN", item.model_number, item.regional_model_number,
            exact.identity if exact else None, item.identity, item.source_url, item.observed_at,
            item.classification_evidence,
        ))

    seen_catalogue: set[tuple[str, str | None]] = set()
    for item in products:
        key = (item.region or "UNKNOWN", item.model_number or item.regional_model_number)
        if key in seen_catalogue or key in support_regional_bases:
            continue
        seen_catalogue.add(key)
        records.append(SamsungReconciliation(
            SamsungRelationship.CATALOGUE_ONLY, item.region or "UNKNOWN", item.model_number,
            item.regional_model_number, item.identity, None, None, None,
        ))
    return tuple(sorted(records, key=lambda item: item.key))


def persist_samsung_reconciliation(store, records: tuple[SamsungReconciliation, ...], *, reconciled_at: datetime | None = None) -> dict[str, object]:
    now = reconciled_at or utc_now()
    connection = store.connection
    baseline = connection.execute("SELECT 1 FROM samsung_reconciliation_runs LIMIT 1").fetchone() is None
    support_regions = sorted({item.region for item in records if item.support_identity})
    onboarded = {row[0] for row in connection.execute(
        "SELECT region FROM source_onboarding WHERE source_kind='samsung_support'"
    )}
    new_regions = set(support_regions) - onboarded
    events = 0
    with connection:
        for region in new_regions:
            connection.execute(
                "INSERT INTO source_onboarding(source_kind,region,first_baselined_at) VALUES('samsung_support',?,?)",
                (region, now.isoformat()),
            )
        cursor = connection.execute(
            "INSERT INTO samsung_reconciliation_runs(reconciled_at,baseline,source_regions_json) VALUES(?,?,?)",
            (now.isoformat(), int(baseline), json.dumps(support_regions)),
        )
        run_id = int(cursor.lastrowid)
        for item in records:
            connection.execute(
                "INSERT INTO samsung_reconciliation_records(run_id,relationship,region,base_model,regional_sku,data_json) VALUES(?,?,?,?,?,?)",
                (run_id, item.relationship.value, item.region, item.base_model, item.regional_sku,
                 json.dumps(asdict(item), sort_keys=True, default=str)),
            )
            if not item.support_identity or not item.regional_sku:
                continue
            candidate_key = f"{item.region}:{item.regional_sku}"
            existing = connection.execute(
                "SELECT state,first_seen,catalogue_first_seen FROM prelaunch_candidates WHERE candidate_key=?",
                (candidate_key,),
            ).fetchone()
            if item.relationship is SamsungRelationship.CATALOGUE_AND_SUPPORT and not existing:
                continue
            if item.relationship is SamsungRelationship.SUPPORT_ONLY_BASE_MODEL:
                state = CandidateState.GLOBAL_UNKNOWN_SUPPORT_MODEL
            elif item.relationship is SamsungRelationship.SUPPORT_ONLY_REGIONAL_SKU:
                state = CandidateState.KNOWN_GLOBALLY_SUPPORT_FIRST_IN_REGION
            elif item.relationship is SamsungRelationship.AMBIGUOUS_SUPPORT_ONLY:
                state = CandidateState.AMBIGUOUS_OFFICIAL_IDENTIFIER
            else:
                state = CandidateState.COMMERCIALLY_CONFIRMED
            first_seen = item.support_observed_at or now
            catalogue_first_seen = now if state is CandidateState.COMMERCIALLY_CONFIRMED else None
            pending_event: tuple[str, dict[str, str]] | None = None
            if existing:
                first_seen = datetime.fromisoformat(existing["first_seen"])
                if existing["catalogue_first_seen"]:
                    catalogue_first_seen = datetime.fromisoformat(existing["catalogue_first_seen"])
                if existing["state"] != state.value and state is CandidateState.COMMERCIALLY_CONFIRMED:
                    pending_event = ("SUPPORT_BEFORE_PRODUCT", {"relationship": item.relationship.value})
            elif not baseline and item.region not in new_regions and state is not CandidateState.COMMERCIALLY_CONFIRMED:
                event_type = "SUPPORT_MODEL_FIRST_SEEN" if state is CandidateState.GLOBAL_UNKNOWN_SUPPORT_MODEL else "SUPPORT_REGIONAL_SKU_FIRST_SEEN"
                pending_event = (event_type, {"relationship": item.relationship.value})
            connection.execute("""
                INSERT INTO prelaunch_candidates(candidate_key,base_model,regional_sku,region,support_url,first_seen,last_seen,
                    classification_evidence_json,state,matched_catalogue_json,catalogue_first_seen,onboarding_baseline)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_key) DO UPDATE SET
                    last_seen=excluded.last_seen, state=excluded.state,
                    matched_catalogue_json=excluded.matched_catalogue_json,
                    catalogue_first_seen=COALESCE(prelaunch_candidates.catalogue_first_seen,excluded.catalogue_first_seen)
            """, (candidate_key, item.base_model, item.regional_sku, item.region, item.support_url,
                    first_seen.isoformat(), now.isoformat(), json.dumps(item.classification_evidence), state.value,
                    json.dumps({"product_identity": item.product_identity}) if item.product_identity else None,
                    catalogue_first_seen.isoformat() if catalogue_first_seen else None,
                    int(baseline or item.region in new_regions)))
            if pending_event:
                connection.execute(
                    "INSERT INTO samsung_candidate_events(candidate_key,event_type,occurred_at,evidence_json) VALUES(?,?,?,?)",
                    (candidate_key, pending_event[0], now.isoformat(), json.dumps(pending_event[1])),
                )
                events += 1
    return {"run_id": run_id, "baseline": baseline, "new_source_regions": sorted(new_regions), "events": events,
            "relationships": len(records)}
