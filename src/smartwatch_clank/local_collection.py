from __future__ import annotations

import threading
from datetime import datetime, timezone

from .core.lock import RunLock, RunLockError
from .core.runner import Runner
from .core.store import SQLiteStore
from .intelligence.samsung import persist_samsung_reconciliation, reconcile_samsung


class LocalCollectionController:
    def __init__(self, config, registry):
        self.config, self.registry = config, registry
        self.allowed = tuple(config.production_allowlist)
        self._guard = threading.Lock()
        self._state = {"state":"idle", "source":None, "started_at":None, "finished_at":None,
                       "message":"Ready for manual local collection.", "outcomes":None, "reconciliation":None}

    def snapshot(self):
        with self._guard: return dict(self._state)

    def start(self, source: str):
        if source not in self.allowed: return False, {"error":"source_not_allowed"}
        with self._guard:
            if self._state["state"] in {"queued","running"}:
                return False, {"error":"collection_already_running", **self._state}
            self._state = {"state":"queued", "source":source, "started_at":None, "finished_at":None,
                           "message":"Collection queued.", "outcomes":None, "reconciliation":None}
        threading.Thread(target=self._run, args=(source,), daemon=True).start()
        return True, self.snapshot()

    def _run(self, source):
        with self._guard:
            self._state.update(state="running", started_at=datetime.now(timezone.utc).isoformat(),
                               message="Collecting from the public source…")
        try:
            with RunLock(self.config.database), SQLiteStore(self.config.database) as store:
                outcomes = Runner(self.registry, store, self.config.runner).run_selected(
                    (source,), self.allowed, {"mode":"field_test_manual"}
                )
                reconciliation = None
                if store.has_healthy_run("samsung_product_catalogue"):
                    names = tuple(item.name for item in self.registry.all() if item.name.startswith("samsung_support_"))
                    reconciliation = persist_samsung_reconciliation(
                        store, reconcile_samsung(
                            store.latest_healthy_observations(("samsung_product_catalogue",)),
                            store.latest_healthy_observations(names),
                        )
                    )
            result = outcomes[0]
            state = "success" if result.healthy and not result.warning else ("degraded" if result.healthy else "failed")
            payload = [{"collector":result.collector,"healthy":result.healthy,"baseline":result.baseline,
                        "observations":result.observation_count,"discoveries":result.discovery_count,
                        "warning":result.warning,"error":result.error}]
            message = "Collection completed; dashboard state refreshed." if result.healthy else (result.error or "Collection failed.")
            with self._guard: self._state.update(state=state, outcomes=payload, reconciliation=reconciliation,
                message=message, finished_at=datetime.now(timezone.utc).isoformat())
        except RunLockError:
            with self._guard: self._state.update(state="already_running", message="Another local collection is already running.", finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            with self._guard: self._state.update(state="failed", message=f"{type(exc).__name__}: {exc}", finished_at=datetime.now(timezone.utc).isoformat())


def run_finalized(config, registry, names: tuple[str, ...] | None = None) -> dict:
    """Synchronously run one or more "finalized" (production_allowlist)
    collectors, under a single `RunLock` acquisition -- backs both the
    "Run all finalized collectors" and individual per-collector run
    operator actions.

    ``names`` defaults to the full `production_allowlist` ("Run All");
    passing a single-element tuple runs just that collector. Either way,
    membership in `config.production_allowlist` is the ONLY criterion for
    what counts as finalized -- exactly matching the canonical
    `python -m smartwatch_clank.cli run --mode production` selection.
    `Runner.run_selected` raises `ValueError` for any name that is not
    both production-tier AND allowlisted, so an experimental/soak collector
    can never be reached through this function, by construction -- there is
    no parameter that could widen the selection.

    Reuses the same reconciliation step the CLI's production run and the
    single-source `LocalCollectionController` already perform, so the
    dashboard's Samsung Regional Matrix stays consistent regardless of
    which surface triggered the run.
    """
    allowed = tuple(config.production_allowlist)
    selected = names if names is not None else allowed
    with RunLock(config.database), SQLiteStore(config.database) as store:
        outcomes = Runner(registry, store, config.runner).run_selected(
            selected, allowed, {"mode": "field_test_run_finalized"}
        )
        reconciliation = None
        if store.has_healthy_run("samsung_product_catalogue"):
            names = tuple(item.name for item in registry.all() if item.name.startswith("samsung_support_"))
            reconciliation = persist_samsung_reconciliation(
                store, reconcile_samsung(
                    store.latest_healthy_observations(("samsung_product_catalogue",)),
                    store.latest_healthy_observations(names),
                )
            )
    return {
        "outcomes": [
            {"collector": r.collector, "healthy": r.healthy, "baseline": r.baseline,
             "observations": r.observation_count, "discoveries": r.discovery_count,
             "warning": r.warning, "error": r.error}
            for r in outcomes
        ],
        "reconciliation": reconciliation,
    }
