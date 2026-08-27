from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(os.environ.get("SMARTWATCH_CLANK_CONFIG_ROOT", Path(__file__).resolve().parents[2]))


def default_database_path() -> Path:
    configured = os.environ.get("SMARTWATCH_CLANK_DB")
    return Path(configured) if configured else PROJECT_ROOT / "var" / "smartwatch-clank.sqlite3"


def config_path(name: str) -> Path:
    return PROJECT_ROOT / "config" / name


def default_qc_archive_path(database: Path | None = None) -> Path:
    """Separate on-disk QC decision archive, physically distinct from the
    live collector database (a different .sqlite3 file, never a table in
    it). Lives beside whichever database is actually configured (same
    reasoning as `default_garmin_catalogue_cache_path`), so it moves with
    persistent state and survives a repo checkout being replaced."""
    base = database if database is not None else default_database_path()
    return base.parent / "smartwatch-clank-qc.sqlite3"


def default_garmin_catalogue_cache_path() -> Path:
    """Persistent classified-product-ID cache, next to whatever DB is configured.

    Garmin's product sitemap has no watch-scoped subset (see docs/stage-c-report.md)
    -- classifying all ~4,300 entries costs one full crawl. Caching stable
    classification decisions (not transient fetch errors) here means only new
    sitemap entries get fetched on subsequent runs. Lives beside the database
    path so it moves with persistent state (e.g. SMARTWATCH_CLANK_DB pointing
    outside the disposable repo checkout on Hetzner), not inside the repo.
    """
    return default_database_path().parent / "garmin_catalogue_cache.json"
