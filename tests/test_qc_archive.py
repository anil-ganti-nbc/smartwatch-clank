from __future__ import annotations

import sqlite3

import pytest

from smartwatch_clank.qc_archive import QC_DECISIONS, AlreadyDecided, QCArchive


def _sample_discovery(discovery_id: int = 1) -> dict:
    return {
        "id": discovery_id, "run_id": 7, "collector": "samsung_product_catalogue",
        "identity": "SM-L300", "change_type": "NEW_REFERENCE", "confidence": "HIGH",
        "editorial_level": "STRONG", "source_url": "https://official.example/SM-L300",
        "discovered_at": "2026-08-27T00:00:00+00:00",
        "previous_json": None, "current_json": '{"model_number":"SM-L300"}',
        "evidence_json": '{"signals":["catalogue_listing"]}',
    }


def test_migrate_creates_separate_file(tmp_path):
    main_db = tmp_path / "smartwatch-clank.sqlite3"
    main_db.write_text("not a real db, just proving the archive is a distinct file")
    archive_path = tmp_path / "smartwatch-clank-qc.sqlite3"
    archive = QCArchive(archive_path)
    archive.migrate()
    assert archive_path.exists()
    assert archive_path != main_db
    # a real, independently-openable sqlite file
    con = sqlite3.connect(archive_path)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "qc_decisions" in tables


def test_decide_persists_full_snapshot_and_provenance(tmp_path):
    archive = QCArchive(tmp_path / "qc.sqlite3")
    archive.migrate()
    archive.decide(_sample_discovery(), "USEFUL")
    row = archive.decision_for(1)
    assert row is not None
    assert row["decision"] == "USEFUL"
    assert row["collector"] == "samsung_product_catalogue"
    assert row["run_id"] == 7
    assert row["source_url"] == "https://official.example/SM-L300"
    assert row["evidence_json"] == '{"signals":["catalogue_listing"]}'
    assert row["decided_at"]


def test_duplicate_decision_raises_already_decided_not_a_crash(tmp_path):
    archive = QCArchive(tmp_path / "qc.sqlite3")
    archive.migrate()
    archive.decide(_sample_discovery(), "USEFUL")
    with pytest.raises(AlreadyDecided):
        archive.decide(_sample_discovery(), "NOT_USEFUL")
    # the original decision is untouched -- no partial/lost update
    assert archive.decision_for(1)["decision"] == "USEFUL"


def test_unknown_decision_rejected(tmp_path):
    archive = QCArchive(tmp_path / "qc.sqlite3")
    archive.migrate()
    with pytest.raises(ValueError):
        archive.decide(_sample_discovery(), "MAYBE")


def test_four_qc_decisions_defined():
    assert set(QC_DECISIONS) == {"USEFUL", "NOT_USEFUL", "FALSE_POSITIVE", "OUT_OF_STOCK"}


def test_decided_discovery_ids_and_recent_and_status(tmp_path):
    archive = QCArchive(tmp_path / "qc.sqlite3")
    archive.migrate()
    archive.decide(_sample_discovery(1), "USEFUL")
    archive.decide(_sample_discovery(2), "OUT_OF_STOCK")
    assert archive.decided_discovery_ids() == {1, 2}
    recent = archive.recent(10)
    assert len(recent) == 2
    assert {row["decision"] for row in recent} == {"USEFUL", "OUT_OF_STOCK"}
    status = archive.status()
    assert status["total"] == 2
    assert status["USEFUL"] == 1
    assert status["OUT_OF_STOCK"] == 1


def test_restart_safety_reopening_reads_same_state(tmp_path):
    path = tmp_path / "qc.sqlite3"
    first = QCArchive(path)
    first.migrate()
    first.decide(_sample_discovery(), "FALSE_POSITIVE")
    # simulate process restart: a brand-new QCArchive instance over the same file
    second = QCArchive(path)
    assert second.decision_for(1)["decision"] == "FALSE_POSITIVE"
    assert second.decided_discovery_ids() == {1}
