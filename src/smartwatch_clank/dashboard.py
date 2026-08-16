"""Local, read-only field-test dashboard for canonical Smartwatch state."""
from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .configuration import load_runtime_config
from .core.store import SQLiteStore
from .operations import candidates_report, health_report, recent_discoveries, reconciliation_report
from .runtime_bridge import identity


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _link(url: str | None) -> str:
    return f'<a href="{_esc(url)}" target="_blank" rel="noreferrer">source</a>' if url else "—"


def _table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> str:
    head = "".join(f"<th>{_esc(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{item}</td>" for item in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>" if rows else "<p class=muted>Nothing recorded yet.</p>"


def render_dashboard(database, registry) -> str:
    with SQLiteStore(database) as store:
        health = health_report(store, registry, load_runtime_config())
        discoveries = recent_discoveries(store, 25)["discoveries"]
        candidates = candidates_report(store, limit=100)["candidates"]
        reconciliation = reconciliation_report(store, limit=100)["relationships"]
        runs = store.connection.execute("SELECT collector,finished_at,healthy,observation_count,warning,error FROM runs ORDER BY id DESC LIMIT 20").fetchall()
    ident = identity()
    health_rows = [( _esc(row["collector"]), _esc(row["status"]), _esc(row["last_run"]), _esc(row["warning"] or row["error"] or "—")) for row in health["collectors"]]
    run_rows = [(_esc(r["collector"]), _esc(r["finished_at"]), "healthy" if r["healthy"] else "degraded", _esc(r["observation_count"]), _esc(r["warning"] or r["error"] or "—")) for r in runs]
    candidate_rows = [(_esc(item["base_model"] or "—"), _esc(item["regional_sku"]), _esc(item["region"]), _esc(item["state"]), _esc(item["first_seen"]), _link(item["support_url"])) for item in candidates]
    discovery_rows = [(_esc(item["base_model"] or item["identity"]), _esc(item["regional_sku"] or "—"), _esc(item["region"] or "—"), _esc(item["type"]), _esc(item["first_seen"]), _link(item["source_url"])) for item in discoveries]
    relationship_rows = [(_esc(item.get("base_model") or "—"), _esc(item.get("regional_sku") or "—"), _esc(item.get("region") or "—"), _esc(item.get("relationship") or "—"), _link(item.get("source_url") or item.get("support_url"))) for item in reconciliation]
    return f'''<!doctype html><title>Smartwatch Clank</title><style>
body{{font:14px system-ui;margin:0;background:#10161f;color:#e7edf7}}header{{padding:14px 22px;background:#182333}}main{{max-width:1200px;margin:auto;padding:20px}}.card{{background:#182333;border:1px solid #2d3b50;border-radius:8px;padding:15px;margin:14px 0}}.muted{{color:#9aa9bd}}.warn{{color:#f3bb63}}table{{width:100%;border-collapse:collapse}}td,th{{padding:7px;text-align:left;border-bottom:1px solid #2d3b50}}a{{color:#82b7ff}}</style>
<header><b>Smartwatch Clank · Field Test</b> <span class=muted>revision {_esc(ident['source_revision_short'])} · v{_esc(ident['version'])} · DB {_esc(database)}</span></header><main>
<div class=card><h2>Overall health: {_esc(health['status'])}</h2><p class=warn><b>Interpretation guard:</b> Samsung support presence is evidence of model/region existence, not proof of current retail availability. Catalogue absence is not automatically discontinuation.</p></div>
<div class=card><h2>Source health</h2>{_table(('Source','Status','Latest run','Warning / error'), health_rows)}</div>
<div class=card><h2>Latest runs</h2>{_table(('Source','Finished','State','Observations','Warning / error'), run_rows)}</div>
<div class=card><h2>Recent watch discoveries</h2>{_table(('Canonical model','Regional SKU','Region','Evidence event','Observed','Link'), discovery_rows)}</div>
<div class=card><h2>Samsung support candidates</h2>{_table(('Base model','Regional SKU','Region','Evidence state','First seen','Support'), candidate_rows)}</div>
<div class=card><h2>Catalogue / support reconciliation</h2>{_table(('Base model','Regional SKU','Region','Relationship','Evidence'), relationship_rows)}</div>
</main>'''


def serve(host: str = "127.0.0.1", port: int = 8300) -> ThreadingHTTPServer:
    config = load_runtime_config()
    from .collectors import default_registry
    registry = default_registry()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlparse(self.path).path not in {"/", "/healthz"}:
                self.send_error(404); return
            if urlparse(self.path).path == "/healthz":
                body = json.dumps({"status": "ok", "database": str(config.database.resolve())}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body); return
            body = render_dashboard(config.database, registry).encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)
        def log_message(self, *_): pass
    return ThreadingHTTPServer((host, port), Handler)
