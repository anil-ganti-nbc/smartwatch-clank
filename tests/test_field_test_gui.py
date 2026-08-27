from __future__ import annotations

from pathlib import Path
import threading
import urllib.error
from urllib.request import Request, urlopen

import pytest

from smartwatch_clank.configuration import load_runtime_config
from smartwatch_clank.core.store import SQLiteStore
from smartwatch_clank.dashboard import render_dashboard, serve
from smartwatch_clank.collectors import default_registry


def test_default_database_path_is_unchanged(monkeypatch):
    monkeypatch.delenv("SMARTWATCH_CLANK_DATA_DIR", raising=False)
    monkeypatch.delenv("SMARTWATCH_CLANK_DB", raising=False)
    assert load_runtime_config().database.name == "smartwatch-clank.sqlite3"
    assert load_runtime_config().database.parent.name == "var"


def test_explicit_field_test_directory_drives_canonical_database(monkeypatch, tmp_path):
    field_state = tmp_path / "Application Support" / "Smartwatch Clank"
    monkeypatch.delenv("SMARTWATCH_CLANK_DB", raising=False)
    monkeypatch.setenv("SMARTWATCH_CLANK_DATA_DIR", str(field_state))
    config = load_runtime_config()
    assert field_state.is_dir()
    assert config.database == field_state / "smartwatch-clank.sqlite3"


def test_dashboard_renders_empty_and_populated_canonical_state(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTWATCH_CLANK_DATA_DIR", str(tmp_path / "state"))
    config = load_runtime_config()
    with SQLiteStore(config.database): pass
    empty = render_dashboard(config.database, default_registry())
    assert "No runs recorded yet" in empty
    assert "support presence is model/region evidence" in empty.lower()
    with SQLiteStore(config.database) as store:
        store.connection.execute("INSERT INTO prelaunch_candidates(candidate_key,base_model,regional_sku,region,support_url,first_seen,last_seen,classification_evidence_json,state,matched_catalogue_json,catalogue_first_seen,onboarding_baseline) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("SM-L100","SM-L100","SM-L100N","India","https://example.invalid/support","2026-01-01","2026-01-02","[\"official_support_sitemap\"]","GLOBAL_UNKNOWN_SUPPORT_MODEL",None,None,0))
        store.connection.commit()
    populated = render_dashboard(config.database, default_registry())
    assert "SM-L100N" in populated and "example.invalid/support" in populated


def test_field_test_dashboard_has_four_manual_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTWATCH_CLANK_DATA_DIR", str(tmp_path / "state"))
    config = load_runtime_config()
    with SQLiteStore(config.database): pass
    page = render_dashboard(config.database, default_registry(), controller=object())
    assert "Collection disabled" in page
    assert "/api/local-collection/run" not in page
    for source in ("samsung_product_catalogue", "samsung_support_de", "samsung_support_gb", "samsung_support_in"):
        assert source in page


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_dashboard_rejects_non_loopback_bind(host):
    with pytest.raises(ValueError, match="must be loopback"):
        serve(host=host, port=0)


def test_dashboard_rejects_unauthenticated_collection_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTWATCH_CLANK_DATA_DIR", str(tmp_path / "state"))
    server = serve(port=0, controller=object())
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/local-collection/run",
            data=b"{}", method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urlopen(request, timeout=3)
        assert exc.value.code == 403
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def _insert_discovery(store: SQLiteStore, *, identity: str = "SM-L300") -> int:
    run_id = store.connection.execute(
        "INSERT INTO runs(collector,started_at,finished_at,healthy,observation_count,warning,error) "
        "VALUES('samsung_product_catalogue','2026-08-27T00:00:00+00:00','2026-08-27T00:01:00+00:00',1,1,NULL,NULL)"
    ).lastrowid
    discovery_id = store.connection.execute(
        "INSERT INTO discoveries(run_id,collector,identity,change_type,confidence,editorial_level,source_url,"
        "discovered_at,previous_json,current_json,evidence_json) VALUES(?, 'samsung_product_catalogue', ?, "
        "'NEW_REFERENCE','HIGH','STRONG','https://official.example/model','2026-08-27T00:01:00+00:00',"
        "NULL,'{}','{}')",
        (run_id, identity),
    ).lastrowid
    store.connection.commit()
    return discovery_id


def _post(server, path: str, host: str = "127.0.0.1") -> tuple[int, str]:
    request = Request(f"http://{host}:{server.server_port}{path}", data=b"{}", method="POST")
    try:
        response = urlopen(request, timeout=3)
        return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_local_operator_mode_authorizes_allowlisted_mutation_routes(monkeypatch, tmp_path):
    """With local_operator=True, a loopback POST to an allowlisted route
    (here: a QC decision) is authorized where the Phase-0 default would
    403 -- and a route NOT on the explicit allowlist stays 403 even under
    local_operator, proving the unlock is narrow, not a blanket one."""
    monkeypatch.setenv("SMARTWATCH_CLANK_DATA_DIR", str(tmp_path / "state"))
    config = load_runtime_config()
    with SQLiteStore(config.database) as store:
        discovery_id = _insert_discovery(store)

    server = serve(port=0, controller=object(), local_operator=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        # Allowlisted route: authorized.
        status, body = _post(server, f"/api/qc/decide/{discovery_id}?decision=USEFUL")
        assert status == 200, body
        assert '"status": "decided"' in body or '"status":"decided"' in body

        # Not on the explicit allowlist: still 403 even with local_operator=True.
        status, _ = _post(server, "/api/something-unreviewed")
        assert status == 403
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_qc_decision_removes_item_from_active_queue_and_appears_in_recently_qced(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTWATCH_CLANK_DATA_DIR", str(tmp_path / "state"))
    config = load_runtime_config()
    with SQLiteStore(config.database) as store:
        discovery_id = _insert_discovery(store)

    before = render_dashboard(config.database, default_registry(), local_operator=True)
    assert "SM-L300" in before  # present in the active queue before QC

    server = serve(port=0, controller=object(), local_operator=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, _ = _post(server, f"/api/qc/decide/{discovery_id}?decision=OUT_OF_STOCK")
        assert status == 200
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    after = render_dashboard(config.database, default_registry(), local_operator=True)
    assert "No active leads" in after or "SM-L300" not in after.split('id=queue')[1].split('id=qced')[0]
    assert "OUT OF STOCK" in after.upper()


def test_qc_decision_is_idempotent_against_duplicate_submission(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTWATCH_CLANK_DATA_DIR", str(tmp_path / "state"))
    config = load_runtime_config()
    with SQLiteStore(config.database) as store:
        discovery_id = _insert_discovery(store)

    server = serve(port=0, controller=object(), local_operator=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        first_status, _ = _post(server, f"/api/qc/decide/{discovery_id}?decision=USEFUL")
        second_status, second_body = _post(server, f"/api/qc/decide/{discovery_id}?decision=NOT_USEFUL")
        assert first_status == 200
        assert second_status == 409
        assert "already_decided" in second_body
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    # Original decision unchanged -- no lost update from the rejected duplicate.
    from smartwatch_clank.paths import default_qc_archive_path
    from smartwatch_clank.qc_archive import QCArchive
    archive = QCArchive(default_qc_archive_path(config.database))
    assert archive.decision_for(discovery_id)["decision"] == "USEFUL"


def test_run_one_rejects_non_finalized_collector_even_under_local_operator(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTWATCH_CLANK_DATA_DIR", str(tmp_path / "state"))
    server = serve(port=0, controller=object(), local_operator=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, body = _post(server, "/api/local-collection/run/garmin_catalogue")
        assert status == 400
        assert "not_finalized" in body
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_selected_runner_rejects_non_production_source(tmp_path):
    from smartwatch_clank.core.runner import Runner
    config = load_runtime_config()
    registry = default_registry()
    with SQLiteStore(tmp_path / "state.sqlite3") as store:
        try:
            Runner(registry, store, config.runner).run_selected(("not-real",), config.production_allowlist)
        except KeyError:
            pass
        else:
            raise AssertionError("unknown source was accepted")
