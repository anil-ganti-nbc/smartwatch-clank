# Smartwatch Clank

Smartwatch Clank is an independently runnable intelligence collector for connected wrist-worn computing devices. It stores durable source observations, computes deterministic changes, and protects editorial alerts from broken collectors.

Stage 2.2 enables controlled production observation for the Samsung product catalogue and independently isolated India/UK/Germany support collectors. Discord notifications remain disabled; the database and CLI are the soak observation surface.

## Quick start

Python 3.11 or newer is required. From the repository root:

```shell
python -m unittest discover -s tests -v
$env:PYTHONPATH="src"; python -m smartwatch_clank.cli identity
$env:PYTHONPATH="src"; python -m smartwatch_clank.cli run --mode production
```

By default runtime data is written to `var/smartwatch-clank.sqlite3`. Override it with `--database` or `SMARTWATCH_CLANK_DB`.

## Safety model

- A collector's first healthy result is a silent baseline.
- Failed, empty, or catastrophically shrunken catalogues never replace the last healthy catalogue.
- Collector failures are persisted and isolated from other collectors.
- Production status and production allowlisting are separate gates.
- Observations and discovery evidence are retained in SQLite.

See `config/scope.yaml` for device scope and `config/config.yaml` for runner defaults.
Samsung source research and limitations are recorded in `docs/samsung-stage2-research.md`.
Stage 2.1 hardening and live results are recorded in `docs/samsung-stage21-report.md`.

## Production operation

Canonical scheduler command (run from the repository root):

```shell
python -m smartwatch_clank.cli run --mode production
```

The command uses `var/smartwatch-clank.sqlite3` and an adjacent atomic run lock.
Each invocation writes a shared cycle ID and host identity into every collector run's metadata. A changed host ID records a migration and the real elapsed observation gap; it never creates a new baseline or fabricates missed runs.

Operational inspection:

```shell
python -m smartwatch_clank.cli scope
python -m smartwatch_clank.cli health
python -m smartwatch_clank.cli discoveries recent
python -m smartwatch_clank.cli candidates
python -m smartwatch_clank.cli reconciliation
python -m smartwatch_clank.cli soak summary --days 30
python -m smartwatch_clank.cli soak report --days 28
python -m smartwatch_clank.cli backup var/backups/smartwatch-clank-transfer.sqlite3
```

Repository defaults come from `config/config.yaml`. An optional ignored `config/local.yaml` can override values deterministically. `identity` and `scope` report the resolved provenance.

## Portable soak runner and host transfer

The cross-platform scheduled entry point is:

```shell
python -m smartwatch_clank.soak_runner
```

It invokes the canonical production command, uses the database-adjacent application lock, writes daily UTF-8 logs under `var/logs/soak`, and retains logs for 30 days. An operating-system scheduler should launch this command; no soak validity depends on Task Scheduler.

Before moving hosts, disable the old scheduler and create a consistent backup while no run owns the lock:

```shell
python -m smartwatch_clank.cli backup var/backups/smartwatch-clank-transfer.sqlite3
```

Transfer that backup as `var/smartwatch-clank.sqlite3` on the new host together with the code and configuration. Do not transfer the adjacent `.lock` file. Set `SMARTWATCH_CLANK_HOST_ID` to a stable, unique host label if the system hostname is not suitable, then start the portable runner on the normal cadence. The first resumed cycle records `from_host_id`, `to_host_id`, migration time, and the observation gap in both migration history and collector-run metadata. Existing healthy snapshots remain authoritative, so no re-baselining occurs. Missed time is reported as a gap and is never backfilled.

The accepted Stage 2.2 database remains the production database. The historical experimental database is not a soak-transfer target.

## Windows production soak automation (temporary host launcher)

The single Windows task `Smartwatch Clank - Samsung Production Soak` invokes the portable runner at 00:30, 02:30, 04:30, 06:30, 08:30, 10:30, 12:30, 14:30, 16:30, 18:30, 20:30, and 22:30 local time. This is only the launcher for the current Windows host, which is expected to be retired after 14 August 2026.

```powershell
.\scripts\install_samsung_soak_task.ps1
.\scripts\samsung_soak_status.ps1
.\scripts\run_samsung_production_soak.ps1  # manual direct cycle
.\scripts\uninstall_samsung_soak_task.ps1
```

Daily logs are written under `var/logs/soak` and retained for 30 days. SQLite history is never removed by log retention or task uninstallation. Task Scheduler ignores a duplicate scheduled instance; the platform-agnostic database lock remains authoritative for overlaps from any launcher.
