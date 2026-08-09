from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone

from .core.models import CollectorTier


def scope_report(registry, config) -> dict[str, object]:
    allowed = set(config.production_allowlist)
    return {
        "production_allowlist": list(config.production_allowlist),
        "collectors": [{
            "collector": item.name, "tier": item.tier.value,
            "production_enabled": item.tier is CollectorTier.PRODUCTION and item.name in allowed,
        } for item in registry.all()],
        "configuration": config.provenance(),
    }


def health_report(store, registry, config) -> dict[str, object]:
    allowed = set(config.production_allowlist)
    collectors = []
    for item in registry.all():
        latest = store.connection.execute(
            "SELECT * FROM runs WHERE collector=? ORDER BY id DESC LIMIT 1", (item.name,)
        ).fetchone()
        successes = store.connection.execute(
            "SELECT * FROM runs WHERE collector=? AND healthy=1 ORDER BY id DESC LIMIT 2", (item.name,)
        ).fetchall()
        success = successes[0] if successes else None
        previous_success = (successes[1] if latest and latest["healthy"] and len(successes) > 1
                            else success if latest and not latest["healthy"] else None)
        failures = 0
        for row in store.connection.execute(
            "SELECT healthy FROM runs WHERE collector=? ORDER BY id DESC", (item.name,)
        ):
            if row["healthy"]:
                break
            failures += 1
        if latest is None:
            status = "NEVER_RUN"
        elif not latest["healthy"]:
            status = "FAILED"
        elif latest["warning"]:
            status = "WARNING"
        else:
            status = "HEALTHY"
        collectors.append({
            "collector": item.name, "tier": item.tier.value,
            "production_enabled": item.tier is CollectorTier.PRODUCTION and item.name in allowed,
            "status": status, "last_run": latest["finished_at"] if latest else None,
            "last_run_id": latest["id"] if latest else None,
            "last_successful_run": success["finished_at"] if success else None,
            "observation_count": latest["observation_count"] if latest else None,
            "last_healthy_count": success["observation_count"] if success else None,
            "previous_healthy_count": previous_success["observation_count"] if previous_success else None,
            "warning": latest["warning"] if latest else None, "error": latest["error"] if latest else None,
            "consecutive_failures": failures,
        })
    overall = "FAILED" if any(item["status"] == "FAILED" for item in collectors) else (
        "WARNING" if any(item["status"] in {"WARNING", "NEVER_RUN"} for item in collectors) else "HEALTHY"
    )
    return {"status": overall, "database": str(store.path.resolve()), "collectors": collectors}


def recent_discoveries(store, limit: int = 50) -> dict[str, object]:
    rows = store.connection.execute("""
        SELECT d.change_type,d.collector,d.identity,d.source_url,d.discovered_at,d.evidence_json,o.data_json
        FROM discoveries d LEFT JOIN observations o ON o.run_id=d.run_id AND o.identity=d.identity
        ORDER BY d.id DESC LIMIT ?
    """, (limit,)).fetchall()
    items = []
    for row in rows:
        observation = json.loads(row["data_json"]) if row["data_json"] else {}
        items.append({
            "type": row["change_type"], "collector": row["collector"], "region": observation.get("region"),
            "base_model": observation.get("model_number"), "regional_sku": observation.get("regional_model_number"),
            "identity": row["identity"], "first_seen": row["discovered_at"], "source_url": row["source_url"],
            "evidence": json.loads(row["evidence_json"]), "baseline_suppressed": False,
        })
    return {"database": str(store.path.resolve()), "count": len(items), "discoveries": items}


def candidates_report(store, state: str | None = None, limit: int = 500) -> dict[str, object]:
    query = "SELECT * FROM prelaunch_candidates"
    params: list[object] = []
    if state:
        query += " WHERE state=?"
        params.append(state)
    query += " ORDER BY base_model,region,regional_sku LIMIT ?"
    params.append(limit)
    rows = store.connection.execute(query, params).fetchall()
    items = [{
        "state": row["state"], "region": row["region"], "base_model": row["base_model"],
        "regional_sku": row["regional_sku"], "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "support_url": row["support_url"], "classification_evidence": json.loads(row["classification_evidence_json"]),
        "catalogue_first_seen": row["catalogue_first_seen"], "matching_catalogue": json.loads(row["matched_catalogue_json"]) if row["matched_catalogue_json"] else None,
        "baseline_suppressed": bool(row["onboarding_baseline"]),
    } for row in rows]
    return {"database": str(store.path.resolve()), "count": len(items), "candidates": items}


def reconciliation_report(store, relationship: str | None = None, limit: int = 500) -> dict[str, object]:
    latest = store.connection.execute("SELECT MAX(id) FROM samsung_reconciliation_runs").fetchone()[0]
    if latest is None:
        return {"database": str(store.path.resolve()), "run_id": None, "count": 0, "relationships": []}
    query = "SELECT * FROM samsung_reconciliation_records WHERE run_id=?"
    params: list[object] = [latest]
    if relationship:
        query += " AND relationship=?"
        params.append(relationship)
    query += " ORDER BY relationship,region,base_model,regional_sku LIMIT ?"
    params.append(limit)
    rows = store.connection.execute(query, params).fetchall()
    return {"database": str(store.path.resolve()), "run_id": latest, "count": len(rows),
            "relationships": [json.loads(row["data_json"]) for row in rows]}


def soak_summary(store, days: int = 30) -> dict[str, object]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = store.connection.execute("SELECT * FROM runs WHERE started_at>=? ORDER BY id", (cutoff,)).fetchall()
    per_collector: dict[str, dict[str, object]] = {}
    for collector in sorted({row["collector"] for row in rows}):
        group = [row for row in rows if row["collector"] == collector]
        durations = [(datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds()
                     for row in group]
        successful_finishes = sorted(datetime.fromisoformat(row["finished_at"]) for row in group if row["healthy"])
        gaps = [(right - left).total_seconds() for left, right in zip(successful_finishes, successful_finishes[1:])]
        per_collector[collector] = {
            "runs": len(group), "successful": sum(bool(row["healthy"]) for row in group),
            "warnings": sum(bool(row["warning"]) for row in group), "failed": sum(not row["healthy"] for row in group),
            "observation_count_min": min((row["observation_count"] for row in group), default=None),
            "observation_count_max": max((row["observation_count"] for row in group), default=None),
            "discoveries": sum(row["discovery_count"] for row in group),
            "duration_seconds_min": min(durations, default=None), "duration_seconds_max": max(durations, default=None),
            "duration_seconds_median": statistics.median(durations) if durations else None,
            "longest_successful_observation_gap_seconds": max(gaps, default=None),
        }
    discovery_types = Counter(row[0] for row in store.connection.execute(
        "SELECT change_type FROM discoveries WHERE discovered_at>=?", (cutoff,)
    ))
    failed = sum(not row["healthy"] for row in rows)
    latest_finish = max((datetime.fromisoformat(row["finished_at"]) for row in rows), default=None)
    now = datetime.now(timezone.utc)
    migrations = [dict(row) for row in store.connection.execute(
        "SELECT from_host_id,to_host_id,recorded_at,observation_gap_seconds "
        "FROM soak_host_migrations WHERE recorded_at>=? ORDER BY id", (cutoff,)
    )]
    return {
        "database": str(store.path.resolve()), "period_days": days, "total_runs": len(rows),
        "successful_runs": sum(bool(row["healthy"]) for row in rows), "warning_runs": sum(bool(row["warning"]) for row in rows),
        "failed_runs": failed, "failure_rate": failed / len(rows) if rows else 0.0,
        "success_rate": sum(bool(row["healthy"]) for row in rows) / len(rows) if rows else 0.0,
        "total_discoveries": sum(row["discovery_count"] for row in rows), "discovery_types": dict(discovery_types),
        "candidate_events": store.connection.execute(
            "SELECT COUNT(*) FROM samsung_candidate_events WHERE occurred_at>=?", (cutoff,)
        ).fetchone()[0], "per_collector": per_collector,
        "active_host_id": store.get_soak_state("active_host_id"),
        "host_migrations": migrations,
        "current_observation_gap_seconds": ((now - latest_finish).total_seconds() if latest_finish else None),
        "safety_triggers": {
            "catastrophic_zero_or_collapse": sum(
                bool(row["error"] and ("zero observations" in row["error"] or "catalogue collapse" in row["error"]))
                for row in rows
            )
        },
    }
