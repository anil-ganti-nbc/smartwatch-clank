# Hetzner deployment — 2026-08-18: Stage A + Stage B merge and rollout

## Merge status

- **Stage A**: [PR #9](https://github.com/anil-ganti-nbc/smartwatch-clank/pull/9), merged (merge commit `7231d77`).
- **Stage B**: [PR #10](https://github.com/anil-ganti-nbc/smartwatch-clank/pull/10), merged (merge commit `22c0e01`).
- **Fix (deploy/run.sh mode)**: [PR #11](https://github.com/anil-ganti-nbc/smartwatch-clank/pull/11), merged (`41aa6dd`) — found during this deployment: `deploy/run.sh` was silently running the compose file's default `--mode production` instead of `--mode experimental`.
- **Fix (soak timer schedule)**: [PR #12](https://github.com/anil-ganti-nbc/smartwatch-clank/pull/12), merged (`4f27ba4`) — switched to a fixed-clock `OnCalendar` schedule chosen by inspecting the live fleet, plus `User=deploy`/journal logging to match every other soak unit already on the host.
- All four merges used real merge commits (`--merge`, not squash), preserving both stages' full commit history in `main`.

**Final `main` commit: `4f27ba41a0c3e4f2035be1f1030492999b4e457c`**

## Commit parity

| | Commit |
|---|---|
| Local `main` | `4f27ba41a0c3e4f2035be1f1030492999b4e457c` |
| GitHub `main` | `4f27ba41a0c3e4f2035be1f1030492999b4e457c` |
| Hetzner git checkout (`/home/deploy/staging/smartwatch-clank`) | `4f27ba41a0c3e4f2035be1f1030492999b4e457c` |
| Hetzner Docker image label (`org.opencontainers.image.revision`) | `4f27ba41a0c3e4f2035be1f1030492999b4e457c` |
| Hetzner running `cli identity` `source_revision` | `4f27ba41a0c3e4f2035be1f1030492999b4e457c` |

**All five match.** Deployed via `git fetch`/`git checkout <exact SHA>` on the host, never SCP.

## Installation layout

- **Path**: `/home/deploy/staging/smartwatch-clank/` (existing convention, matches every other Clank on this box; no new layout invented).
- **Database**: named Docker volume `smartwatch_clank_staging_data`, mounted at `/app/data/smartwatch-clank.sqlite3` inside containers — persistent state, outside the disposable git checkout, unaffected by re-deploys.
- **Logs**: `/home/deploy/staging/smartwatch-clank/logs/cron-YYYYMMDD.log` for the existing production-cron path; `journalctl -u smartwatch-clank-soak.service` for the new experimental-soak timer path.
- **Python environment**: isolated venv at `/home/deploy/staging/smartwatch-clank/.venv` (stdlib-only project, zero runtime deps; `pytest` installed for verification). Used for direct host-level test/CLI verification; the actual scheduled execution path remains Docker, matching the existing production convention.
- **Backups**: `/home/deploy/staging/smartwatch-clank/backups/smartwatch-clank-20260818T123556Z.sqlite3`, taken before this deployment, verified readable (432 runs, 46192 observations, 52 discoveries at backup time).

## systemd units

- `smartwatch-clank-soak.service` — oneshot, `User=deploy`/`Group=deploy`, runs `deploy/run.sh` (which now explicitly passes `run --mode experimental`), journal-logged, matches `korean-tech-wire-soak.service`/`oem-radar-experimental-sitemap-soak.service` conventions already on this host.
- `smartwatch-clank-soak.timer` — `OnCalendar=*-*-* 00/2:12:00` (every 2 hours, minute :12), `RandomizedDelaySec=90`, `Persistent=true`. Chosen by inspecting `systemctl list-timers --all` and `sudo crontab -u deploy -l` at deploy time: existing offsets in use were :05, :10, :15, :18, :20, :36, :40; the existing smartwatch-clank **production** cron itself runs at :50 past *odd* hours (`50 1-23/2 * * *`) — this timer deliberately runs at *even* hours so the two schedules never land in the same wall-clock minute.
- Both `enabled` (survive reboot) and `active`. Next scheduled fire at deploy time: **2026-08-18 14:12:28 UTC**.
- The existing production cron entry (`50 1-23/2 * * * .../deploy_run.sh`, driving `--mode production`, i.e. just the 4 Samsung collectors) is **completely untouched** — confirmed via `sudo crontab -u deploy -l` before and after.

## Collectors registered vs. participating in soak

- **8 collectors registered**: `samsung_product_catalogue`, `samsung_support_in`, `samsung_support_gb`, `samsung_support_de` (all `PRODUCTION` tier), `samsung_official_news`, `google_official_news`, `garmin_official_news`, `apple_official_news` (all `EXPERIMENTAL` tier).
- **Production allowlist**: unchanged from before this session — still exactly the 4 Samsung collectors above. **Not empty** (see note below).
- **Experimental soak (the new timer)**: runs `--mode experimental`, which covers **all 8** registered collectors regardless of tier (`CollectorRegistry.selected()` returns everything in experimental mode) — including re-running the 4 already-production Samsung collectors, harmlessly, since `RunLock` serializes any overlap with the separate production-cron path against the same database.

## Manual verification runs (before installing the timer)

Four full `run --mode experimental` cycles were executed against the real, backed-up production database via the deployed Docker image (one was an accidental extra retry on my end after a JSON-parsing script failed to parse mixed stdout — the underlying cycle still completed and persisted correctly, which only strengthened the verification):

| Cycle | Healthy | Failed | New discoveries | Notes |
|---|---|---|---|---|
| 1 | 7/8 | 1 (garmin_official_news) | 0 | All previously-known collectors correctly `baseline=false`; the 4 new news collectors correctly `baseline=true` (silent first-seen, per spec) |
| 2 (discarded output, real data) | 7/8 | 1 | 0 | — |
| 3 | 7/8 | 1 | 0 | All collectors now `baseline=false`; `evidence_records.first_seen` confirmed unchanged from cycle 1 while `last_seen` advanced |
| 4 (systemd `systemctl start`, simulating the timer) | 7/8 | 1 | 0 | Full JSON output captured in `journalctl -u smartwatch-clank-soak.service`; `runs` table shows correct `run_uuid`/`app_version`/`schema_version_at_run`/`config_fingerprint`/`git_revision` provenance for every collector in this cycle |

**Zero false first-seen or discovery spam across all four cycles.** `runs` table grew by exactly 32 rows (4 cycles × 8 collectors), `samsung_reconciliation` stayed steady at 394 relationships with 0 new events throughout, and all pre-existing historical data (432 runs / 46192 observations / 52 discoveries at backup time) is fully intact.

### Known issue found: Garmin Newsroom blocks this Hetzner IP

`garmin_official_news` fails with `HTTP 403` from the Hetzner host on every cycle (confirmed working correctly from a residential/dev-machine IP during Stage B research). Root cause confirmed directly: Garmin's newsroom is behind Cloudflare bot protection and returns a `cf-mitigated` "Just a moment..." challenge page — `403` even with a full browser `User-Agent` string, meaning this is IP-reputation/datacenter-based blocking, not a header/UA problem fixable in the collector code. This is the failure-isolation design working exactly as intended: the other 7 collectors are entirely unaffected, `collector_health` correctly marks only `garmin_official_news` unhealthy, and the systemd service reports "failed" (exit code 1) purely because the CLI's own health-based exit code correctly propagates a genuine partial failure — not a deployment defect. Left as a known, documented limitation; not fixed in this session per the "no Stage C work" instruction.

## Test counts

- **Local** (merged `main`, `.venv-codex`): **99 passed**, 0 failures.
- **Hetzner** (`.venv` at `/home/deploy/staging/smartwatch-clank/.venv`, full suite, not just a subset): **99 passed**, 0 failures, 4.17s.

## Production allowlist / Discord confirmation

- **Discord delivery: confirmed disabled.** `notifications_enabled: false` in `cli identity` output, both locally and on Hetzner (Docker and venv). `notifications/discord.py`'s `DiscordNotifier.notify()` still unconditionally raises `NotImplementedError`; nothing in this deployment wired it up.
- **Production allowlist: confirmed unchanged, but *not empty*.** It contains the same 4 Samsung collectors it has held since before this session's expansion work (`samsung_product_catalogue`, `samsung_support_in`, `samsung_support_gb`, `samsung_support_de`) — this predates Stage A/B and was already the live production state (`SMARTWATCH_CLANK_DEFINITION_OF_DONE.md`'s "Current production state" section). **None of the Stage A/B additions were added to it** — all 4 new collectors are `EXPERIMENTAL` and only run via the new soak timer, never via the production-tier path. If "empty" was intended literally, that would mean *demoting* the existing Samsung production collectors, which was not requested and was not done.

## Deployment/portability issues found

1. `deploy/run.sh` silently defaulted to `--mode production` instead of `--mode experimental` (fixed, PR #11, caught before the timer was ever installed).
2. `deploy/smartwatch-clank-soak.timer.example`'s original `OnUnitActiveSec`-relative schedule didn't give an auditable fixed clock minute; switched to `OnCalendar` and the service unit's `User=`/journal settings to match the real fleet convention only discoverable by inspecting the live host (fixed, PR #12).
3. Garmin Newsroom's Cloudflare protection blocks this Hetzner IP (see above) — documented, not fixed, out of scope for this session.
4. Cosmetic: `docker compose` warns that the existing named volume was created under Compose project name `smartwatch-clank-test` rather than `smartwatch-clank` (a naming artifact from the original 2026-08-09 deployment). Harmless — Compose still correctly reuses the existing volume by name every time — but worth a note if it's ever consolidated.
