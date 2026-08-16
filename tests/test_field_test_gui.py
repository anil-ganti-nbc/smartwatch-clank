from __future__ import annotations

from pathlib import Path

from smartwatch_clank.configuration import load_runtime_config
from smartwatch_clank.core.store import SQLiteStore
from smartwatch_clank.dashboard import render_dashboard
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
