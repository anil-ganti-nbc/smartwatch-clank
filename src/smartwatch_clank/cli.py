from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from pathlib import Path

from .collectors import default_registry
from .configuration import load_runtime_config
from .core.lock import RunLock, RunLockError
from .core.models import RunScope
from .core.registry import CollectorRegistry
from .core.runner import Runner, RunProvenance
from .core.qualification import ExecutionProvenance
from .core.soak import prepare_soak_cycle
from .core.store import SQLiteStore
from .intelligence.news import persist_news_evidence
from .intelligence.samsung import persist_samsung_reconciliation, reconcile_samsung
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
            with lock_context, SQLiteStore(database) as store:
                run_metadata = prepare_soak_cycle(store, mode=args.mode)
                runtime_identity = identity()
                provenance = RunProvenance(
                    app_version=runtime_identity["version"],
                    config_fingerprint=config.config_fingerprint,
                    git_revision=runtime_identity["source_revision"],
                    trigger=args.trigger,
                )
                outcomes = Runner(registry, store, config.runner, provenance).run(
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
                _print({
                    "mode": args.mode, "trigger": provenance.trigger.value,
                    "database": str(database), "lock_enabled": not args.no_lock,
                    "cycle": run_metadata,
                    "collectors_run": len(outcomes), "healthy": sum(item.healthy for item in outcomes),
                    "failed": sum(not item.healthy for item in outcomes), "samsung_reconciliation": reconciliation,
                    "official_news_evidence": official_news_evidence,
                    "outcomes": [{
                        "collector": item.collector, "healthy": item.healthy, "baseline": item.baseline,
                        "observations": item.observation_count, "discoveries": item.discovery_count,
                        "warning": item.warning, "error": item.error,
                    } for item in outcomes],
                })
            return 1 if any(not item.healthy for item in outcomes) else 0
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

    with SQLiteStore(database) as store:
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
