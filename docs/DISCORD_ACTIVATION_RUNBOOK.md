# Discord delivery activation runbook (Hetzner)

Smartwatch Clank's Discord delivery is a persistence-first outbox. Code
readiness is not activation: nothing turns delivery on, chooses a cutoff, or
configures a webhook by itself. This is the deliberate operator procedure.
It mirrors the fleet-proven feature-phone-clank runbook, adapted to this
clank's schema-state authority (the `schema_version` marker, currently
v3 → v4).

Every step before step 7 is read-only or local-config only: nothing contacts
Discord until the explicitly authorized test send.

---

## 0. Preconditions

- You have decided that delivery should be enabled at all.
- You have a Discord webhook URL for the target channel, **not yet** written
  anywhere on the host.
- You accept the ambiguous-delivery limitation in §9.

## 1. Deploy the code and confirm the revision

The established transport: `git fetch` / `git checkout <exact SHA>` in
`/home/deploy/staging/smartwatch-clank`, build the image tagged with the
full SHA, then flip `.deployed-id` (both the production cron at
`50 1-23/2 * * *` and the soak timer read it at invocation). Do this in a
quiet window between scheduled runs, and do NOT flip `.deployed-id` until §4
is complete — a scheduled run must never be the first, unobserved live
migration.

## 2. Inspect the live database and its pending queue — read-only

The live database is the named Docker volume
`smartwatch_clank_staging_data` mounted at
`/app/data/smartwatch-clank.sqlite3` (≈400 MB). Open it read-only:

```bash
docker run --rm -v smartwatch_clank_staging_data:/data \
  --entrypoint python3 smartwatch-clank:<OLD-IMAGE-TAG> -c "
import sqlite3
con = sqlite3.connect('file:/data/smartwatch-clank.sqlite3?mode=ro', uri=True)
print('schema_version:', con.execute('SELECT version FROM schema_version WHERE id=1').fetchone()[0])
print('notifications:', con.execute('SELECT COUNT(*) FROM notifications').fetchall() if
      con.execute(\"SELECT name FROM sqlite_master WHERE name='notifications'\").fetchone() else 'table absent')
print('discoveries by level:', con.execute(
    'SELECT editorial_level, COUNT(*) FROM discoveries GROUP BY editorial_level').fetchall())"
```

Decide from what you read, not from any doc.

## 3. Take a verified, SQLite-safe backup

A file copy of a live database is not a backup. Use the online backup API,
then integrity-check both sides and record the SHA-256:

```bash
docker run --rm -v smartwatch_clank_staging_data:/data \
  --entrypoint python3 smartwatch-clank:<NEW-IMAGE-TAG> -c "
import sqlite3, hashlib, os
src = '/data/smartwatch-clank.sqlite3'
dst = '/data/backups/smartwatch-clank-pre-discord-activation.db'
s, d = sqlite3.connect(src), sqlite3.connect(dst)
with d: s.backup(d)
s.close(); d.close()
c = sqlite3.connect(dst)
print('integrity:', c.execute('PRAGMA integrity_check').fetchone()[0])
c.close()
print('sha256:', hashlib.sha256(open(dst,'rb').read()).hexdigest())"
```

Do not continue unless `integrity_check` is `ok`. Keep this file; it is the
rollback for §4.

## 4. Apply the v3 → v4 migration deliberately

`EXPECTED_SCHEMA_VERSION` moves 3 → 4: two new tables (`notifications`
outbox, `delivery_policy`) — additive only, no existing table is altered.
The migration runs automatically the first time ANY read-write
`SQLiteStore` opens the database, which is exactly why an ordinary
scheduled collector invocation must not be the first one to do it
unobserved. Do it on purpose instead, with §3's backup in hand, in a quiet
window (between the soak timer at :12 even hours and the production cron at
:50 odd hours):

```bash
cd /home/deploy/staging/smartwatch-clank
IMAGE_TAG=$(cat .deployed-id)   # still the OLD revision
docker compose -f docker-compose.staging.yml run --rm smartwatch-clank \
    --database /app/data/smartwatch-clank.sqlite3 diagnose-notifications
```

Then verify read-only: `schema_version` = 4, tables `notifications` and
`delivery_policy` exist, `runs`/`observations`/`discoveries` row counts
unchanged, `PRAGMA integrity_check` = ok.

The deliberate migration command is any invocation that opens the store
read-write. `diagnose-notifications`, `deliver --preview`, and
`notifications` are inspection-class (read-only) and never migrate — use
`delivery-activation` (which opens the store read-write and then prints the
activation policy) as in the sequence above.

**Consequence:** once migrated, any older binary expecting v3 refuses the
database with INCOMPATIBLE_NEWER and fails closed. That is the gate
working. Flip `.deployed-id` to the new SHA only after this verification,
so every subsequent scheduled process opens the already-migrated database
with compatible code.

## 5. Preview the proposed cutoff — still read-only, still sends nothing

```bash
IMAGE_TAG=$(cat .deployed-id) docker compose -f docker-compose.staging.yml \
    run --rm smartwatch-clank deliver --preview --cutoff "<NOW UTC ISO-8601>"
```

For a future-only activation `would_send` must be **0** at the moment you
set it; `would_hold` covers whatever is queued (usually nothing yet).
`provenance_gaps` should be all zeros. Iterate on `--cutoff` until
`would_send` is what you actually intend. This command never writes, never
sends, and never resolves the webhook URL.

## 6. Install the cutoff, then the secret outside Git

Install the policy FIRST so the queue is gated before a webhook exists on
the host:

```bash
IMAGE_TAG=$(cat .deployed-id) docker compose -f docker-compose.staging.yml \
    run --rm smartwatch-clank delivery-activation --set "<NOW UTC ISO-8601>"
IMAGE_TAG=$(cat .deployed-id) docker compose -f docker-compose.staging.yml \
    run --rm smartwatch-clank delivery-activation   # configured: true, unreadable: false
```

Then the secret. It is read from `SMARTWATCH_CLANK_DISCORD_WEBHOOK_URL`,
environment-only — never config.yaml/local.yaml, never the image, never Git,
never logs. The compose file already forwards the variable (without any
value); compose substitution reads the untracked `.env` file next to
docker-compose.staging.yml, exactly like the fleet's other clanks:

```bash
sudo install -m 0600 -o deploy -g deploy /dev/null /home/deploy/staging/smartwatch-clank/.env
# edit .env to contain exactly one line:
# SMARTWATCH_CLANK_DISCORD_WEBHOOK_URL=<paste the URL>
chmod 600 /home/deploy/staging/smartwatch-clank/.env
```

Verify the value never appears in `git grep`, `journalctl`, or the clank's
own JSON output (`diagnose-notifications` reports only
`webhook_configured: true/false`).

## 7. One labelled test send — only when you authorize it

The first moment anything reaches Discord. It posts an unmistakable
`SMARTWATCH CLANK — TEST` embed referencing no real discovery:

```bash
IMAGE_TAG=$(cat .deployed-id) docker compose -f docker-compose.staging.yml \
    run --rm smartwatch-clank test-notify --confirm-production \
    --note "activation check $(date -u +%FT%TZ)"
```

Expect `{"sent": true, ...}` and exactly one test message in the channel.
Failures come back as bounded categories (`connection_error`,
`http_unauthorized`, `http_404_webhook_not_found`, …) — never the URL.

Then prove the backlog is still held and untouched:

```bash
IMAGE_TAG=$(cat .deployed-id) docker compose -f docker-compose.staging.yml \
    run --rm smartwatch-clank deliver --preview
```

`counts_by_status` must be unchanged and held rows must still show
`attempts = 0`.

## 8. Verify later natural delivery

Do NOT force a drain to prove it works. Let the next scheduled run produce a
genuine post-cutoff discovery and deliver it on its own. If a `429`
appears, the drain stops itself, records a durable `not_before` floor on
that row, and does not burn an attempt — wait for the window rather than
retrying manually.

## 9. Standing limitations and gates

- **Eligibility.** Production-tier AND allowlisted collectors only;
  editorial levels CRITICAL and NEWSWORTHY only. MONITOR and NOISE never
  create a notification row — they stay dashboard/QC-only. Baseline runs
  produce no discoveries and therefore no notifications; discoveries from
  unhealthy collector runs are never persisted, so they can never enqueue.
  Measured live volume at activation design time (14 days): 158 eligible
  discoveries ≈ 11/day — moderate for a channel, no flood, no narrowing.
- **Activation cutoff.** Drain-time policy: any queued row older than the
  installed cutoff is marked `held` (attempts untouched). Held history
  leaves only via explicit `deliver --include-held` — an operator act,
  never automatic.
- **Successful sends never repeat.** A `sent` row is never re-selected; the
  UNIQUE(provider, dedup_key) contract additionally blocks re-enqueueing
  the same discovery. Not exactly-once: a crash between Discord accepting
  a POST and the row being marked `sent` can produce one duplicate on the
  next drain. Any outbox that records after sending has this window.
- **429 handling.** Retry-After honoured up to 15 minutes; longer or
  non-numeric falls back to 60 seconds. The drain stops itself for the
  rest of the queue rather than hammering Discord. Five consecutive
  non-429 failures mark a row terminally `failed` (recoverable via
  `deliver --requeue-failed`).
- **Collection independence.** A Discord outage never turns a healthy
  collection run unhealthy: the post-run drain is fully nonfatal, and
  failed sends stay pending/retryable in the durable outbox.
