"""Local desktop field-test dashboard for canonical Smartwatch state.

Read-only by default (Phase 0). A supported local launcher
(`native/windows/launcher.py`, `native/macos/launcher.py`) may pass
`local_operator=True` to deliberately unlock a narrow, loopback-only set of
operator mutations -- see `local_operator.py`'s module docstring for the
full rationale and the closed-ended route allowlist. Every other way of
starting this server (bare `serve()`, tests, a hand-run `python -c ...`)
stays fail-closed: no GUI launch ever runs a collector on its own, and no
route outside the allowlist ever mutates anything, regardless of the
`local_operator` flag.
"""
from __future__ import annotations

import html, ipaddress, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .configuration import load_runtime_config
from .core.lock import RunLockError
from .core.schema_state import SchemaStateError
from .core.store import SQLiteStore
from .local_collection import run_finalized
from .local_operator import request_is_local_operator_mutation
from .operations import candidates_report, health_report, recent_discoveries, reconciliation_report
from .paths import default_qc_archive_path
from .qc_archive import QC_DECISIONS, AlreadyDecided, QCArchive
from .runtime_bridge import identity

from smartwatch_clank.collector_ui import (
    CSS as UI_CSS, DESIGN_SYSTEM_VERSION,
    badge as _ui_badge, empty as _ui_empty,
)

# Smartwatch Clank domain accent (the only visual token this Clank overrides).
ACCENT, ACCENT_SOFT = "#31c48d", "#0f2b22"

LABELS = {"samsung_product_catalogue": ("Samsung Product Catalogue", "CATALOGUE"), "samsung_support_de": ("Samsung Support Germany", "SUPPORT"), "samsung_support_gb": ("Samsung Support UK", "SUPPORT"), "samsung_support_in": ("Samsung Support India", "SUPPORT")}

def e(value: object) -> str: return html.escape("" if value is None else str(value))
def link(url: str | None) -> str: return f'<a href="{e(url)}" title="{e(url)}" target="_blank" rel="noreferrer">Open source ↗</a>' if url else "—"
def badge(value: str) -> str:
    # Collector UI v1: map this Clank's health vocabulary onto the shared
    # family vocabulary so the same state reads identically across the fleet.
    canonical = {"HEALTHY": "HEALTHY", "WARNING": "DEGRADED",
                 "FAILED": "FAILED", "NEVER_RUN": "UNKNOWN"}.get(value, value)
    return _ui_badge(canonical)
def table(headers, rows, title, detail):
    if not rows:
        return _ui_empty(title, detail)
    head = ''.join(f'<th>{e(x)}</th>' for x in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{x}</td>' for x in row) + '</tr>' for row in rows)
    return ('<div class="tablewrap"><table class="t"><thead><tr>' + head
            + '</tr></thead><tbody>' + body + '</tbody></table></div>')


def _active_queue(store: SQLiteStore, qc: QCArchive, limit: int = 200) -> list[dict]:
    """Discoveries (post-baseline evidence changes) with no QC decision yet
    on file in the SEPARATE archive DB. The live `discoveries` table is
    never filtered/mutated by QC -- membership here is computed purely by
    excluding ids already present in `qc.decided_discovery_ids()`, so a
    restart or a dashboard reload always reconstructs the same queue from
    on-disk state, never from anything in memory."""
    decided = qc.decided_discovery_ids()
    rows = store.connection.execute("""
        SELECT d.id,d.run_id,d.collector,d.identity,d.change_type,d.confidence,d.editorial_level,
               d.source_url,d.discovered_at,d.previous_json,d.current_json,d.evidence_json,o.data_json
        FROM discoveries d LEFT JOIN observations o ON o.run_id=d.run_id AND o.identity=d.identity
        ORDER BY d.id DESC LIMIT ?
    """, (limit,)).fetchall()
    items = []
    for row in rows:
        if row["id"] in decided:
            continue
        observation = json.loads(row["data_json"]) if row["data_json"] else {}
        items.append({**dict(row), "region": observation.get("region"),
                      "base_model": observation.get("model_number"), "regional_sku": observation.get("regional_model_number")})
    return items


def _item_detail(item: dict) -> str:
    evidence = json.loads(item["evidence_json"]) if item.get("evidence_json") else {}
    previous = json.loads(item["previous_json"]) if item.get("previous_json") else None
    current = json.loads(item["current_json"]) if item.get("current_json") else None
    body = (
        f'<dl class=detail>'
        f'<dt>Collector</dt><dd>{e(item["collector"])}</dd>'
        f'<dt>Identity</dt><dd>{e(item["identity"])}</dd>'
        f'<dt>Change type</dt><dd>{e(item["change_type"])}</dd>'
        f'<dt>Confidence</dt><dd>{e(item.get("confidence"))}</dd>'
        f'<dt>Editorial level</dt><dd>{e(item.get("editorial_level"))}</dd>'
        f'<dt>Run ID</dt><dd>{e(item.get("run_id"))}</dd>'
        f'<dt>Source</dt><dd>{link(item.get("source_url"))}</dd>'
        f'<dt>Evidence</dt><dd><pre>{e(json.dumps(evidence, indent=2, sort_keys=True))}</pre></dd>'
    )
    if previous is not None or current is not None:
        body += f'<dt>Previous</dt><dd><pre>{e(json.dumps(previous, indent=2, sort_keys=True))}</pre></dd>'
        body += f'<dt>Current</dt><dd><pre>{e(json.dumps(current, indent=2, sort_keys=True))}</pre></dd>'
    return body + '</dl>'


def render_dashboard(database, registry, controller=None, *, local_operator: bool = False) -> str:
    config=load_runtime_config()
    qc = QCArchive(default_qc_archive_path(database))
    qc.migrate()
    with SQLiteStore(database) as store:
        health=health_report(store,registry,config); discoveries=recent_discoveries(store,25)["discoveries"]
        candidates=candidates_report(store,limit=100)["candidates"]; relations=reconciliation_report(store,limit=100)["relationships"]
        runs=store.connection.execute("SELECT collector,finished_at,healthy,observation_count,warning,error FROM runs ORDER BY id DESC LIMIT 20").fetchall()
        queue = _active_queue(store, qc)
    recently_qced = qc.recent(50)
    ident=identity(); never=sum(x["status"]=="NEVER_RUN" for x in health["collectors"]); revision = "local development build" if ident["source_revision_short"] == "unknown" else ident["source_revision_short"]
    sources=[]
    for x in health["collectors"]:
        name,typ=LABELS.get(x["collector"],(x["collector"],"SOURCE")); sources.append((f'<b>{e(name)}</b><small>{e(x["collector"])}</small>',f'<span class="type {typ.lower()}">{typ}</span>',badge(x["status"]),e(x["last_run"] or "—"),e(x["warning"] or x["error"] or "No runs recorded")))
    runrows=[(f'<b>{e(LABELS.get(x["collector"],(x["collector"],))[0])}</b>',e(x["finished_at"]),badge("HEALTHY" if x["healthy"] else "FAILED"),e(x["observation_count"]),e(x["warning"] or x["error"] or "—")) for x in runs]
    discrows=[(e(x["base_model"] or x["identity"]),e(x["regional_sku"] or "—"),e(x["region"] or "—"),e(x["type"]),e(x["first_seen"]),link(x["source_url"])) for x in discoveries]
    candrows=[(e(x["base_model"] or "—"),e(x["regional_sku"]),e(x["region"]),e(x["state"]),e(x["first_seen"]),link(x["support_url"])) for x in candidates]
    relrows=[(e(x.get("base_model") or "—"),e(x.get("regional_sku") or "—"),e(x.get("region") or "—"),e(x.get("relationship") or "—"),link(x.get("source_url") or x.get("support_url"))) for x in relations]

    finalized = tuple(config.production_allowlist)
    experimental = [item.name for item in registry.all() if item.name not in finalized]

    if local_operator:
        collector_buttons = ''.join(
            f'<button class=run onclick="runOne(\'{e(name)}\')" id="run-{e(name)}">▶ Run {e(LABELS.get(name,(name,))[0])}</button>'
            for name in finalized
        )
        collect = f'''<section class="card collect" style="margin-top:14px" id=run-all><h2>Manual collection <span class=pill2>LOCAL OPERATOR</span></h2>
<p class=muted>Local-loopback mutation authority is active for this launch. Runs never fire automatically -- every collection below is operator-initiated.</p>
<div class=runbar><button class="run runall" onclick="runAll()">▶ Run all finalized collectors ({len(finalized)})</button>{collector_buttons}</div>
<pre id=run-output class=runoutput>Ready.</pre>
<details class=soak><summary>Experimental / soak collectors ({len(experimental)}) -- hidden from Run All by design</summary>
<p class=muted>Wired into the registry and runnable individually via the CLI (<code>--mode experimental</code>), but never selectable here and never included in Run All. Promote a source to finalized only by adding it to <code>config/config.yaml</code>'s <code>production_allowlist</code> -- never from this GUI.</p>
<ul>{''.join(f'<li>{e(name)}</li>' for name in experimental)}</ul>
</details></section>'''
    else:
        collect = '' if controller is None else '''<section class="card collect" style="margin-top:14px"><h2>Collection disabled</h2><p class=muted>This dashboard was launched without local-operator mutation authority (read-only Phase 0 mode). Use the approved desktop launcher for manual Run All / per-collector control, or the CLI.</p></section>'''

    queue_rows = []
    for item in queue:
        actions = ''
        if local_operator:
            actions = ''.join(
                f'<button class=qc onclick="qcDecide({item["id"]},\'{d}\')">{d.replace("_"," ").title()}</button>'
                for d in QC_DECISIONS
            )
        queue_rows.append((
            f'<details><summary><b>{e(item.get("base_model") or item["identity"])}</b> <small>{e(item["collector"])}</small></summary>{_item_detail(item)}</details>',
            e(item.get("region") or "—"), e(item["change_type"]), e(item["discovered_at"]),
            link(item.get("source_url")),
            f'<div class=qcbar id="qc-row-{item["id"]}">{actions if actions else "<span class=muted>Read-only</span>"}</div>',
        ))
    queue_table = table(('Item','Region','Change','Discovered','Source','QC'), queue_rows, 'No active leads',
                         'Post-baseline discoveries needing QC will appear here.')

    qced_rows = [(
        e(d["decided_at"]), f'<span class="badge {("good" if d["decision"]=="USEFUL" else "bad" if d["decision"] in ("NOT_USEFUL","FALSE_POSITIVE") else "warn")}">{e(d["decision"].replace("_"," "))}</span>',
        e(d["collector"]), e(d["identity"]), link(d.get("source_url")),
    ) for d in recently_qced]
    qced_table = table(('Decided at','Decision','Collector','Item','Source'), qced_rows, 'Nothing QCed yet',
                        'Items you QC will show up here with their decision and provenance, read from the separate QC archive database.')

    # Deliberately omitted from the page entirely (not merely unused) unless
    # local_operator is True -- a read-only launch must never even reveal
    # the mutation endpoint surface in its markup, matching the fail-closed
    # default the rest of this module holds to.
    script = '' if not local_operator else '''<script>
async function post(url){ const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"}); const t = await r.text(); return {ok:r.ok, status:r.status, body:t}; }
async function runAll(){ const out=document.getElementById("run-output"); out.textContent="Running all finalized collectors..."; const r = await post("/api/local-collection/run-all"); out.textContent = r.body; if(r.ok) setTimeout(()=>location.reload(), 600); }
async function runOne(name){ const out=document.getElementById("run-output"); out.textContent="Running "+name+"..."; const r = await post("/api/local-collection/run/"+encodeURIComponent(name)); out.textContent = r.body; if(r.ok) setTimeout(()=>location.reload(), 600); }
async function qcDecide(id, decision){ const row=document.getElementById("qc-row-"+id); row.innerHTML="Saving..."; const r = await post("/api/qc/decide/"+id+"?decision="+encodeURIComponent(decision)); if(r.ok){ location.reload(); } else { row.innerHTML = "Error: "+r.body; } }
</script>'''

    return f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Smartwatch Clank</title><style>{UI_CSS}
:root{{--accent:{ACCENT};--accent-soft:{ACCENT_SOFT};}}</style></head><body><div class=app>
<header class="topbar"><div class="brand"><span class="brand-mark">SW</span><span class="brand-name">Smartwatch Clank</span><span class="brand-suite">Clank Fleet</span></div><div class="topbar-meta"><span class="mono">rev {e(revision)}</span><span class="mono">db {e(database.name)}</span><span class="badge plain sq">FIELD TEST</span></div></header><div class="body"><nav class="rail"><div class="rail-group first">Monitor</div><a class="nav active" href="#overview">Overview</a><a class="nav" href="#runs">Run History</a><div class="rail-group">Queue</div><a class="nav" href="#queue">Active Queue</a><a class="nav" href="#qced">Recently QCed</a><div class="rail-group">Discovery</div><a class="nav" href="#discoveries">Discoveries</a><a class="nav" href="#candidates">Support Candidates</a><a class="nav" href="#reconciliation">Reconciliation</a><div class="rail-group">System</div><a class="nav" href="#source-health">Sources</a><a class="nav" href="#about">About</a></nav><main class="main"><div class="wrap">
<section class="kpis"><div class="metric health"><div class="kpi-label">OVERALL HEALTH</div><div class="kpi-value">{e(health['status'])}</div><p class=guard><b>Interpretation guard:</b> Support presence is model/region evidence, not proof of current retail availability. Catalogue absence is not discontinuation.</p></div><div class="kpi"><div class="kpi-label">SOURCES</div><div class="kpi-value">{len(health['collectors'])}</div><span class="muted">Total configured</span></div><div class="kpi"><div class="kpi-label">NEVER RUN</div><div class="kpi-value">{never}</div><span class="muted">Awaiting observation</span></div><div class="kpi"><div class="kpi-label">ACTIVE QUEUE</div><div class="kpi-value">{len(queue)}</div><span class="muted">Needs QC</span></div><div class="kpi"><div class="kpi-label">SUPPORT CANDIDATES</div><div class="kpi-value">{len(candidates)}</div><span class="muted">Requires reconciliation</span></div></section>{collect}
<section class="panel pad" id=queue style="margin-top:14px"><h2>Active Lead / Event Queue</h2>{queue_table}</section>
<section class="panel pad" id=qced style="margin-top:14px"><h2>Recently QCed</h2>{qced_table}</section>
<section class="cols two"><div class="panel pad" id=source-health><h2>Source Health <a href=#runs>View history →</a></h2>{table(('Source','Type','Status','Latest run','Warning / error'),sources,'No runs recorded yet','Collector runs will appear here once executed.')}</div><div class="panel pad"><h2>Quick Actions</h2><a href=#source-health>View Source Health<small>Inspect canonical collector status</small></a><a href=#runs>View Run History<small>Inspect recorded local runs</small></a><a href=#reconciliation>Inspect Reconciliations<small>Read-only regional relationships</small></a><a href=#about>Local State Details<small>View field-test provenance</small></a></div></section>
<section class="cols"><div class="panel pad" id=runs><h2>Latest Runs</h2>{table(('Source','Finished','State','Observations','Warning'),runrows,'No runs recorded yet','Collector runs will appear here once executed.')}</div><div class="panel pad" id=discoveries><h2>Recent Watch Discoveries</h2>{table(('Model','SKU','Region','Evidence','Observed','Source'),discrows,'No discoveries yet','New canonical watch discoveries will appear here.')}</div><div class="panel pad" id=candidates><h2>Support Candidates</h2>{table(('Base model','SKU','Region','State','First seen','Source'),candrows,'No support candidates yet','Potential regional support models will appear here.')}</div></section>
<section class="panel pad" id=reconciliation style="margin-top:14px"><h2>Catalogue / Support Reconciliation · Regional Matrix</h2>{table(('Base model','Regional SKU','Region','Relationship','Evidence'),relrows,'No reconciliation rows yet','Catalogue and regional support relationships will appear after canonical runs.')}</section><section class="panel pad" id=about style="margin-top:14px"><div><b>Support evidence</b>Regional support presence indicates model/region existence; it is not retail availability.</div><div><b>Catalogue evidence</b>Catalogue absence can be lag or regional scope; it is not discontinuation.</div><div><b>Owner role</b>Use direct source evidence and reconciliation, not assumptions.</div></section><div class="foot">Field Test Mode · Local data only · No data leaves this machine</div></div></main></div></div>{script}</body></html>'''


def serve(host: str="127.0.0.1", port: int=8300, controller=None, *, local_operator: bool = False) -> ThreadingHTTPServer:
    """Start the loopback dashboard.

    ``local_operator=True`` is the deliberate, narrow Phase-0 mutation
    unlock -- see `local_operator.py`. It must ONLY ever be passed by a
    supported local launcher; every other caller (including this function's
    own default) stays fail-closed and read-only.
    """
    try: loopback = ipaddress.ip_address(host).is_loopback
    except ValueError: loopback = host.lower() == "localhost"
    if not loopback:
        raise ValueError("Smartwatch Clank has no authenticated remote profile; dashboard host must be loopback")
    config=load_runtime_config()
    from .collectors import default_registry
    registry=default_registry()
    qc = QCArchive(default_qc_archive_path(config.database))
    qc.migrate()

    class Handler(BaseHTTPRequestHandler):
        def _authorized(self) -> bool:
            if not local_operator:
                return False
            host_header = self.headers.get("Host")
            return request_is_local_operator_mutation(
                client_host=self.client_address[0], host_header=host_header,
                method="POST", path=urlparse(self.path).path,
            )

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/local-collection/status" and controller:
                self._json(200, controller.snapshot()); return
            if path == "/api/qc/recent":
                self._json(200, {"decisions": qc.recent(50)}); return
            if path not in {"/","/healthz"}: self.send_error(404); return
            if path=="/healthz":
                self._json(200, {"status":"ok","database":str(config.database.resolve()),
                                  "qc_archive":str(qc.path.resolve()),"local_operator":local_operator}); return
            body=render_dashboard(config.database,registry,controller,local_operator=local_operator).encode()
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._authorized():
                self.send_error(403, "Dashboard mutations require local-operator launch authority"); return

            if path == "/api/local-collection/run-all":
                try:
                    result = run_finalized(config, registry)
                except RunLockError as exc:
                    self._json(409, {"error":"already_running","detail":str(exc)}); return
                except Exception as exc:
                    self._json(500, {"error":f"{type(exc).__name__}: {exc}"}); return
                self._json(200, result); return

            if path.startswith("/api/local-collection/run/"):
                name = path.rsplit("/", 1)[-1]
                if name not in config.production_allowlist:
                    self._json(400, {"error":"not_finalized", "collector":name}); return
                try:
                    result = run_finalized(config, registry, (name,))
                except RunLockError as exc:
                    self._json(409, {"error":"already_running","detail":str(exc)}); return
                except Exception as exc:
                    self._json(500, {"error":f"{type(exc).__name__}: {exc}"}); return
                self._json(200, result); return

            if path.startswith("/api/qc/decide/"):
                raw_id = path.rsplit("/", 1)[-1]
                query = urlparse(self.path).query
                params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                decision = unquote(params.get("decision", ""))
                if not raw_id.isdigit():
                    self._json(400, {"error":"invalid_discovery_id"}); return
                discovery_id = int(raw_id)
                if decision not in QC_DECISIONS:
                    self._json(400, {"error":"invalid_decision", "allowed": list(QC_DECISIONS)}); return
                try:
                    store_cm = SQLiteStore(config.database)
                except SchemaStateError as exc:
                    self._json(503, {
                        "error": "state_incompatible",
                        "gate": "persistent_state_compatibility",
                        **exc.report.as_evidence(),
                    })
                    return
                with store_cm as store:
                    row = store.connection.execute(
                        "SELECT id,run_id,collector,identity,change_type,confidence,editorial_level,source_url,"
                        "discovered_at,previous_json,current_json,evidence_json FROM discoveries WHERE id=?",
                        (discovery_id,),
                    ).fetchone()
                if row is None:
                    self._json(404, {"error":"discovery_not_found", "discovery_id": discovery_id}); return
                try:
                    qc.decide(dict(row), decision)
                except AlreadyDecided:
                    existing = qc.decision_for(discovery_id)
                    self._json(409, {"error":"already_decided", "decision": existing}); return
                except ValueError as exc:
                    self._json(400, {"error": str(exc)}); return
                self._json(200, {"status":"decided", "discovery_id": discovery_id, "decision": decision}); return

            self.send_error(404); return

        def log_message(self,*_): pass
    return ThreadingHTTPServer((host,port),Handler)
