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
