from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from .collectors import default_registry
from .configuration import load_runtime_config
from .core.lock import RunLock, RunLockError
from .core.models import RunScope
from .core.registry import CollectorRegistry
from .core.runner import Runner, RunProvenance
from .core.qualification import ExecutionProvenance
from .core.schema_state import SchemaState, SchemaStateError, UNADMITTABLE_STATES, inspect_store
from .core.soak import prepare_soak_cycle
from .core.store import SQLiteStore
from .intelligence.news import persist_news_evidence
from .intelligence.samsung import persist_samsung_reconciliation, reconcile_samsung
from .notifications.discord import resolve_webhook_url
from .operations import (
    candidates_report, health_report, recent_discoveries, reconciliation_report, scope_report, soak_summary,
)
from .runtime_bridge import identity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smartwatch-clank", description="Smartwatch primary-source intelligence collector")
    parser.add_argument("--database", type=Path, help="override the configured database path")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run registered collectors")
    run.add_argument("--mode", choices=[scope.value for scope in RunScope], default="production")
    run.add_argument("--trigger", choices=[item.value for item in ExecutionProvenance],
                     default=ExecutionProvenance.MANUAL.value,
                     help="authoritative execution trigger; scheduler wrappers pass SCHEDULED")
    run.add_argument("--no-lock", action="store_true", help="debug only: bypass the database run lock")
    run.add_argument("--allow-experimental-database", action="store_true", help="debug only: permit production mode on an experimental-named database")
    sub.add_parser("identity", help="show runtime identity, version, database, and config provenance")
    sub.add_parser("health", help="show operational collector health")
    sub.add_parser("scope", help="show collector-level production scope")
    sub.add_parser("collectors", help="alias for scope")
    discoveries = sub.add_parser("discoveries", help="inspect recent persisted discoveries")
    discoveries.add_argument("view", choices=["recent"], nargs="?", default="recent")
    discoveries.add_argument("--limit", type=int, default=50)
    candidates = sub.add_parser("candidates", help="inspect reconciliation candidates")
    candidates.add_argument("--state")
    candidates.add_argument("--limit", type=int, default=500)
    p_test = sub.add_parser(
        "test-notify",
        help="send an unmistakable SMARTWATCH CLANK — TEST notification through the real delivery path",
    )
    p_test.add_argument("--note", default="", help="optional note embedded in the test message")
    p_test.add_argument("--webhook", help="override the webhook target (for testing endpoints); "
                                          "never stored or logged")
    p_test.add_argument("--confirm-production", action="store_true",
                        help="explicit owner approval required when the configured production "
                             "webhook (env) would be contacted")
    p_deliver = sub.add_parser("deliver", help="drain the notification outbox (or preview a cutoff)")
    p_deliver.add_argument("--preview", action="store_true",
                           help="read-only: report what a cutoff would send/hold, touching nothing")
    p_deliver.add_argument("--cutoff", help="proposed activation cutoff for --preview (ISO-8601)")
    p_deliver.add_argument("--include-held", action="store_true",
                           help="explicit operator replay of activation-held history; never the default")
    p_deliver.add_argument("--requeue-failed", action="store_true",
                           help="requeue terminally failed notifications before draining")
    p_notif = sub.add_parser("notifications", help="inspect the notification outbox")
    p_notif.add_argument("--status", help="list recent rows for this status (pending/sent/failed/held)")
    p_notif.add_argument("--limit", type=int, default=50)
    sub.add_parser("diagnose-notifications",
                   help="delivery diagnostics: counts, activation policy, retry floors, provenance gaps")
    p_activation = sub.add_parser(
        "delivery-activation",
        help="show or install the durable Discord activation cutoff (operator decision, not a side effect)",
    )
    p_activation.add_argument("--set", dest="set_cutoff",
                              help="install this ISO-8601 cutoff; discoveries older than it are held")
    reconciliation = sub.add_parser("reconciliation", help="inspect latest cross-source relationships")
    reconciliation.add_argument("--relationship")
    reconciliation.add_argument("--limit", type=int, default=500)
    soak = sub.add_parser("soak", help="summarize production soak history")
    soak.add_argument("view", choices=["summary", "report"], nargs="?", default="summary")
    soak.add_argument("--days", type=int, default=30)
    continuity = sub.add_parser(
        "continuity",
        help="inspect the ADR-0006 continuity registry (restore/gap/epoch evidence)",
    )
    continuity.add_argument(
        "--ensure-seed", action="store_true",
        help="create the registry with operator-verified incident seed records if absent "
             "(append-only; never edits existing records)",
    )
    backup = sub.add_parser("backup", help="create a consistent transferable database backup")
    backup.add_argument("output", type=Path)
    return parser


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _open_writable_store(database: Path):
    """Open a read-write store behind the compatibility barrier. Returns the
    store, or None after printing the machine-readable refusal (exit 3)."""
    try:
        return SQLiteStore(database)
    except SchemaStateError as exc:
        _print({
            "status": "state_incompatible",
            "gate": "persistent_state_compatibility",
            "database": str(database),
            **exc.report.as_evidence(),
        })
        return None


def _drain_after_run(store) -> dict:
    """Best-effort outbox drain at the end of a completed collection run.

    Everything that could reach Discord here is already durable: discoveries
    and their notification intents committed inside save_run. Any problem —
    a Discord outage, a busy delivery grant, an unexpected error — degrades
    to a summary field and never changes the run's own health or exit code.
    """
    try:
        from .notifications.discord import DiscordNotifier, resolve_webhook_url

        return DiscordNotifier(store, resolve_webhook_url()).drain()
    except Exception as exc:  # noqa: BLE001 — delivery must never fail a collection
        return {"status": "nonfatal_error", "category": type(exc).__name__,
                "detail": "delivery drain failed; queued intents remain durable and retryable"}


def cmd_test_notify(args, database: Path) -> int:
    """Send one unmistakable SMARTWATCH CLANK — TEST notification. Refuses to
    contact the configured production webhook without explicit owner approval;
    a fabricated --webhook target needs no approval. Never touches a real
    Discovery row and never releases held history."""
    from .notifications.discord import DISCORD_WEBHOOK_ENV, DiscordNotifier, resolve_webhook_url

    webhook = args.webhook or resolve_webhook_url()
    if webhook and not args.webhook and not args.confirm_production:
        _print({
            "status": "refused",
            "message": f"a webhook is configured via {DISCORD_WEBHOOK_ENV}; sending a test "
                       "notification to it requires --confirm-production (explicit owner "
                       "approval), or pass --webhook to target a different/test endpoint.",
        })
        return 1
    store = _open_writable_store(database)
    if store is None:
        return 3
    try:
        result = DiscordNotifier(store, webhook).enqueue_test(note=args.note or "")
    finally:
        store.close()
    _print({"status": "ok", **result})
    return 0 if result["sent"] else 1


def cmd_deliver(args, database: Path) -> int:
    """Drain the notification outbox, or --preview a proposed cutoff.

    --preview is inspection-class: read-only store, no send, no requeue, no
    policy write, no mutation of any kind — safe against a live database at
    any time. A real drain persists every outcome durably."""
    from .notifications.discord import DiscordNotifier, delivery_preview

    if args.preview:
        report = inspect_store(database)
        if report.state in UNADMITTABLE_STATES:
            _print({"status": "state_incompatible",
                    "gate": "persistent_state_compatibility",
                    "database": str(database), **report.as_evidence()})
            return 3
        if report.state is SchemaState.FRESH:
            _print({"status": "ok", "note": "database not initialized yet",
                    "provider": "discord", "counts_by_status": {}})
            return 0
        with SQLiteStore(database, read_only=True) as store:
            _print(delivery_preview(store, args.cutoff, bool(resolve_webhook_url())))
        return 0
    store = _open_writable_store(database)
    if store is None:
        return 3
    try:
        if args.requeue_failed:
            requeued = store.requeue_failed_notifications("discord")
            _print({"status": "requeued", "count": requeued})
        result = DiscordNotifier(store, resolve_webhook_url()).drain(
            include_held=args.include_held)
    finally:
        store.close()
    _print({"status": "ok", **result})
    return 0


def cmd_notifications(args, database: Path) -> int:
    """Inspect the notification outbox without touching SQLite directly:
    counts by status, plus recent rows for a requested status (payload is
    provider-internal and redacted from operator output)."""
    report = inspect_store(database)
    if report.state in UNADMITTABLE_STATES:
        _print({"status": "state_incompatible",
                "gate": "persistent_state_compatibility",
                "database": str(database), **report.as_evidence()})
        return 3
    if report.state is SchemaState.FRESH:
        _print({"status": "ok", "database": str(database),
                "note": "database not initialized yet", "counts": {}, "notifications": []})
        return 0
    with SQLiteStore(database, read_only=True) as store:
        counts = store.notification_counts("discord")
        rows = []
        if args.status:
            rows = [
                {key: row[key] for key in row.keys() if key != "payload_json"}
                for row in store.notifications_by_status("discord", args.status, args.limit)
            ]
    _print({"status": "ok", "counts": counts, "notifications": rows})
    return 0


def cmd_diagnose_notifications(args, database: Path) -> int:
    """Delivery diagnostics: counts, activation policy, webhook presence
    (boolean only), retry floors in force, failed-row error categories, and
    outbox provenance gaps. Read-only."""
    from .notifications.delivery_policy import ACTIVATION_CUTOFF_KEY, load_policy

    report = inspect_store(database)
    if report.state in UNADMITTABLE_STATES:
        _print({"status": "state_incompatible",
                "gate": "persistent_state_compatibility",
                "database": str(database), **report.as_evidence()})
        return 3
    if report.state is SchemaState.FRESH:
        _print({"status": "ok", "note": "database not initialized yet"})
        return 0
    with SQLiteStore(database, read_only=True) as store:
        provider = "discord"
        counts = {
            row["status"]: row["c"] for row in store.connection.execute(
                "SELECT status, COUNT(*) c FROM notifications WHERE provider=? GROUP BY status",
                (provider,),
            ).fetchall()
        }
        deferred_now = store.connection.execute(
            "SELECT COUNT(*) FROM notifications WHERE provider=? AND status='pending' "
            "AND not_before IS NOT NULL AND not_before > ?",
            (provider, datetime.now(timezone.utc).isoformat()),
        ).fetchone()[0]
        failed_categories: dict[str, int] = {}
        for row in store.notifications_by_status(provider, "failed", limit=10_000):
            category = row["last_error"] or "unknown"
            failed_categories[category] = failed_categories.get(category, 0) + 1
        _print({
            "status": "ok",
            "provider": provider,
            "counts_by_status": counts,
            "activation_policy": load_policy(store.policy_get(ACTIVATION_CUTOFF_KEY)).describe(),
            "webhook_configured": bool(resolve_webhook_url()),
            "retry_floors_in_force": deferred_now,
            "failed_error_categories": failed_categories,
            "provenance_gaps": store.provenance_gap_counts(provider),
        })
    return 0


def cmd_delivery_activation(args, database: Path) -> int:
    """Show, or explicitly install, the durable activation cutoff.

    Installing a cutoff is deliberately its own command: it is an operator
    decision about what history may leave the building, not a side effect of
    running or delivering. An unparseable value is refused and nothing is
    persisted."""
    from .notifications.delivery_policy import ACTIVATION_CUTOFF_KEY, load_policy, parse_timestamp

    if args.set_cutoff is not None and parse_timestamp(args.set_cutoff) is None:
        _print({
            "status": "invalid_cutoff",
            "message": "cutoff must be an ISO-8601 timestamp, e.g. 2026-09-10T00:00:00Z",
            "given": args.set_cutoff,
        })
        return 2
    store = _open_writable_store(database)
    if store is None:
        return 3
    try:
        if args.set_cutoff is not None:
            store.policy_set(ACTIVATION_CUTOFF_KEY, args.set_cutoff)
        policy = load_policy(store.policy_get(ACTIVATION_CUTOFF_KEY))
    finally:
        store.close()
    _print({"status": "ok", "activation_policy": policy.describe()})
    return 0


def main(argv: list[str] | None = None, registry: CollectorRegistry | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    registry = registry or default_registry()
    config = load_runtime_config()
    database = (args.database or config.database).resolve()

    if args.command == "identity":
        output = identity()
        output.update({"database": str(database), "configuration": config.provenance()})
        _print(output)
        return 0
    if args.command == "test-notify":
        return cmd_test_notify(args, database)
    if args.command == "deliver":
        return cmd_deliver(args, database)
    if args.command == "notifications":
        return cmd_notifications(args, database)
    if args.command == "diagnose-notifications":
        return cmd_diagnose_notifications(args, database)
    if args.command == "delivery-activation":
        return cmd_delivery_activation(args, database)
    if args.command == "continuity":
        from .core import continuity as continuity_module

        if args.ensure_seed:
            path = continuity_module.ensure_registry(database)
            events = continuity_module.read_events(database)
        elif continuity_module.registry_path(database).exists():
            path = continuity_module.registry_path(database)
            events = continuity_module.read_events(database)
        else:
            _print({
                "status": "NO_REGISTRY",
                "registry_path": str(continuity_module.registry_path(database)),
                "message": "no continuity registry yet (run --ensure-seed to create it "
                           "with the operator-verified restore/gap seed records)",
            })
            return 1
        bad = continuity_module.verify_hashes(events)
        epochs = sorted({e.get("new_epoch_id") for e in events if e.get("new_epoch_id")})
        _print({
            "status": "OK", "registry_path": str(path), "epochs": epochs,
            "epoch_id": continuity_module.EPOCH_ID,
            "event_count": len(events), "hash_mismatches": bad, "events": events,
        })
        return 1 if bad else 0
    if args.command in {"scope", "collectors"}:
        _print(scope_report(registry, config))
        return 0

    if args.command == "run":
        if args.mode == "production" and "experimental" in database.name.lower() and not args.allow_experimental_database:
            parser.error("production mode refuses an experimental-named database; choose the canonical live path")
        from .core import continuity as continuity_module

        continuity_module.ensure_registry(database)
        lock_context = nullcontext() if args.no_lock else RunLock(database)
        try:
            with lock_context, SQLiteStore(database) as store:  # noqa: E501 (compatibility gate: may raise SchemaStateError -> handled below)
                run_metadata = prepare_soak_cycle(store, mode=args.mode)
                runtime_identity = identity()
                provenance = RunProvenance(
                    app_version=runtime_identity["version"],
                    config_fingerprint=config.config_fingerprint,
                    git_revision=runtime_identity["source_revision"],
                    trigger=args.trigger,
                )
                from .notifications.discord import notification_intent_factory

                outcomes = Runner(
                    registry, store, config.runner, provenance,
                    notification_intent_factory=notification_intent_factory(
                        store, config.production_allowlist),
                ).run(
                    RunScope(args.mode), production_allowlist=config.production_allowlist,
                    run_metadata=run_metadata,
                )
                reconciliation = None
                registered_names = tuple(item.name for item in registry.all())
                support_names = tuple(name for name in registered_names if name.startswith("samsung_support_"))
                if "samsung_product_catalogue" in registered_names and store.has_healthy_run("samsung_product_catalogue"):
                    products = store.latest_healthy_observations(("samsung_product_catalogue",))
                    supports = store.latest_healthy_observations(support_names)
                    reconciliation = persist_samsung_reconciliation(store, reconcile_samsung(products, supports))
                official_news_evidence = {}
                for name in registered_names:
                    if name.endswith("_official_news") and store.has_healthy_run(name):
                        observations = store.latest_healthy_observations((name,))
                        official_news_evidence[name] = persist_news_evidence(store, observations)
                # Delivery happens only after every collector's discoveries
                # AND their notification intents are durably committed. A
                # Discord outage (or any delivery problem) must never turn a
                # healthy collection run unhealthy, so this is fully nonfatal.
                notifications_drain = _drain_after_run(store)
                _print({
                    "mode": args.mode, "trigger": provenance.trigger.value,
                    "database": str(database), "lock_enabled": not args.no_lock,
                    "cycle": run_metadata,
                    "collectors_run": len(outcomes), "healthy": sum(item.healthy for item in outcomes),
                    "failed": sum(not item.healthy for item in outcomes), "samsung_reconciliation": reconciliation,
                    "official_news_evidence": official_news_evidence,
                    "notifications": notifications_drain,
                    "outcomes": [{
                        "collector": item.collector, "healthy": item.healthy, "baseline": item.baseline,
                        "observations": item.observation_count, "discoveries": item.discovery_count,
                        "warning": item.warning, "error": item.error,
                    } for item in outcomes],
                })
            return 1 if any(not item.healthy for item in outcomes) else 0
        except SchemaStateError as exc:
            _print({
                "status": "state_incompatible",
                "gate": "persistent_state_compatibility",
                "database": str(database),
                **exc.report.as_evidence(),
            })
            return 3
        except RunLockError as exc:
            _print({"status": "BLOCKED", "database": str(database), "error": str(exc)})
            return 2

    if args.command == "backup":
        from .core import continuity as continuity_module

        try:
            output = None
            with RunLock(database), SQLiteStore(database, read_only=True) as store:
                # SQLiteStore.backup_to() returns the Path of the written
                # backup; build the summary dict HERE (the serialization
                # boundary) instead of dict()-ing a Path.
                backup_path = store.backup_to(args.output)
                output = {
                    "database": str(database),
                    "output": str(backup_path),
                    "size_bytes": backup_path.stat().st_size,
                }
                continuity_src = continuity_module.registry_path(database)
                if continuity_src.exists():
                    import hashlib

                    raw = continuity_src.read_bytes()
                    continuity_copy = Path(str(args.output) + ".continuity.jsonl")
                    continuity_copy.write_bytes(raw)
                    output["continuity_snapshot"] = {
                        "path": str(continuity_copy),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "size_bytes": len(raw),
                    }
            if output is not None:
                _print({"status": "BACKED_UP", **output})
            return 0
        except RunLockError as exc:
            _print({"status": "BLOCKED", "database": str(database), "error": str(exc)})
            return 2

    # M18: these commands are inspection-class — read-only, never migrating,
    # and refusing unadmittable state with evidence instead of serving it as
    # fact (health on a genuinely fresh database reports "not initialized").
    report = inspect_store(database)
    if report.state in UNADMITTABLE_STATES:
        _print({
            "status": "state_incompatible",
            "gate": "persistent_state_compatibility",
            "database": str(database),
            **report.as_evidence(),
        })
        return 3
    if report.state is SchemaState.FRESH:
        if args.command == "health":
            _print({
                "status": "OK", "operational_state": "unknown",
                "database": str(database),
                "persistent_state": report.as_evidence(),
                "note": "database not initialized yet; run a collection first",
            })
            return 0
        _print({"status": "OK", "database": str(database),
                "note": "database not initialized yet", "results": []})
        return 0
    with SQLiteStore(database, read_only=True) as store:
        if args.command == "health":
            output = health_report(store, registry, config)
        elif args.command == "discoveries":
            output = recent_discoveries(store, args.limit)
        elif args.command == "candidates":
            output = candidates_report(store, args.state, args.limit)
        elif args.command == "reconciliation":
            output = reconciliation_report(store, args.relationship, args.limit)
        else:
            output = soak_summary(store, args.days)
        _print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
