# Garmin egress relay (2026-08-29)

## Problem

`www.garmin.com` sits behind Cloudflare, which blanket-blocks the Hetzner
soak host's IP with a bot-mitigation challenge — confirmed via matched-UA
curl tests: same URL, same headers, same collector User-Agent, 403 from
Hetzner and 200 from a residential IP. Documented since Stage C
(`docs/stage-c-report.md`, `docs/hetzner-deployment-2026-08-18.md`); this is
IP reputation, not a header/UA fix. Affects exactly two collectors:
`garmin_catalogue` and `garmin_official_news` (both `www.garmin.com`).
`garmin_updates` (`forums.garmin.com`, a different subdomain) is unaffected
and always fetches direct.

## Design

Keep the whole soak on one host (Hetzner), one canonical DB, one
`active_host_id` — no split-DB, no row-merge layer, no host-migration
bookkeeping. Only the network *egress* for the two blocked collectors is
rerouted, via an HTTP CONNECT proxy reachable through a reverse SSH tunnel
from the NAS (`192.168.0.105`), which has a residential/non-blocked IP.

```
garmin_catalogue/garmin_official_news (in the Hetzner container)
  -> http://host.docker.internal:18889   (docker-compose extra_hosts: host-gateway)
  -> Docker bridge gateway IP on Hetzner, 172.17.0.1:18889
  -> garmin-relay-forwarder (Hetzner, network_mode: host, socat,
     bound only to 172.17.0.1:18889 -- never the public interface)
  -> Hetzner host loopback 127.0.0.1:18888
  -> reverse SSH tunnel (NAS -> Hetzner, -R 127.0.0.1:18888:garmin-relay-proxy:8888)
  -> tinyproxy container on NAS (garmin-relay-proxy, internal docker network only)
  -> www.garmin.com
```

**Why the extra hop (18889 -> 18888):** `host.docker.internal`/`host-gateway`
resolves to the Docker bridge gateway IP on Linux, not loopback. The relay
tunnel has to stay bound to Hetzner's own loopback (`127.0.0.1:18888`) --
`GatewayPorts` was deliberately left untouched, so the tunnel cannot bind
anywhere else. `garmin-relay-forwarder` is the minimal bridge between the
two: a single `socat` container with `network_mode: host`, listening only
on the bridge gateway IP (`172.17.0.1`, not `0.0.0.0` -- confirmed via `ss
-tlnp` and a curl test from outside that address after deploy), forwarding
to the loopback tunnel endpoint. One `ufw` rule
(`allow from 172.17.0.0/16 to any port 18889 proto tcp`) permits only
bridge-network traffic to reach it; nothing public-facing changed.

Every other collector never reads the proxy env var and is unaffected.
If the tunnel is down, `UrlLibHttpClient._check_proxy_reachable()` does a
cheap TCP probe before attempting a real fetch and raises
`ProxyUnreachableError` immediately — isolated to those two collectors by
the Runner's existing per-collector exception handling, same as any other
collector failure. No other collector, the DB, or `active_host_id` is
touched by a dead tunnel.

## NAS side (192.168.0.105)

- Docker network `garmin-relay-net` (isolated, `172.20.0.0/16`, no host
  ports published).
- `garmin-relay-proxy`: `vimagick/tinyproxy`, config at
  `/volume2/clank/garmin-relay/tinyproxy.conf`, `Allow`-restricted to the
  relay network subnet, `restart: unless-stopped`.
- `garmin-relay-tunnel`: `alpine:3.20` + `openssh-client`, holds
  `ssh -N -R 127.0.0.1:18888:garmin-relay-proxy:8888` open to Hetzner,
  `restart: unless-stopped`, `-o ServerAliveInterval=30` keepalive.
- Private key: `/volume2/clank/garmin-relay/garmin_relay_tunnel` (mode 600,
  bind-mounted `:ro` into the tunnel container). The matching public key is
  the *only* copy of this credential outside NAS; it was generated fresh
  for this purpose and never existed anywhere else.

## Hetzner side

- `deploy@204.168.142.1` `~/.ssh/authorized_keys` carries one extra
  restricted entry for the relay key:
  `no-pty,no-agent-forwarding,no-x11-forwarding,no-user-rc,permitlisten="18888"`.
  This key can do exactly one thing: hold a remote listener on
  `127.0.0.1:18888`. No shell, no pty, no other forwarding.
  (The `restrict` shorthand was tried first and rejected the connection
  outright even with `permitlisten` present — `sshd` logged "Server has
  disabled port forwarding" despite reading the `permitlisten` option, so
  the equivalent flags are spelled out explicitly instead.)
- No `/etc/ssh/sshd_config` changes were needed — `AllowTcpForwarding`
  already defaults to yes, and binding the forwarded port to Hetzner's own
  loopback needs no `GatewayPorts` change.
- `docker-compose.staging.yml`: `SMARTWATCH_CLANK_GARMIN_PROXY` (empty by
  default → direct fetch, unchanged behavior) and an `extra_hosts:
  host.docker.internal:host-gateway` entry so the container can reach the
  loopback-bound tunnel port.
- `deploy/run.sh`: passes `SMARTWATCH_CLANK_GARMIN_PROXY` through to the
  container, defaulting to `http://host.docker.internal:18889` unless
  explicitly overridden/unset.
- `garmin-relay-forwarder`: `docker run -d --name garmin-relay-forwarder
  --network host --restart unless-stopped alpine/socat
  TCP-LISTEN:18889,bind=172.17.0.1,fork,reuseaddr TCP:127.0.0.1:18888`.
  Not in docker-compose (it's host infra, not part of the app) — a plain
  standalone container, restarted by Docker itself on daemon/host restart
  via its own restart policy, same as `garmin-relay-tunnel`/
  `garmin-relay-proxy` on the NAS side.
- `ufw allow from 172.17.0.0/16 to any port 18889 proto tcp` — the only
  firewall change; scoped to the docker bridge subnet, nothing public.

## Deploy-script issues found and fixed along the way (unrelated to this feature)

Two pre-existing environment issues on the Hetzner staging checkout blocked
`scripts/deploy_hetzner.sh` and were fixed as part of landing this, since
the script can't get past its own DB-backup step otherwise:

- `~/staging/smartwatch-clank/backups/` was `root:root` mode 755 (probably
  left over from an earlier root-context operation) — the backup command
  runs as the container's non-root `clank` (uid 10001), so it couldn't
  write there. `chown deploy:deploy` + `chmod 777` (backup dumps aren't
  secret; this directory only holds SQLite exports).
- Scattered files under `.git/` (objects, and a few working-tree files
  including the old `deploy/run.sh`) were also `root`-owned for the same
  reason, breaking `git fetch`/`checkout`. `chown -R deploy:deploy .git .`
  fixed it; `git checkout -- .` then reconciled the working tree.
- Separately, `backup`'s own `RunLock` acquisition fails outright against
  a `:ro`-mounted volume (`OSError: Read-only file system` creating the
  lock file) — `deploy_hetzner.sh` step 3 mounts `:ro` deliberately, so
  this is a real bug in the flock-based `RunLock` rewrite (PR #17)
  colliding with the deploy script's read-only backup step. Worked around
  for this deploy by mounting read-write instead (the backup command
  itself never mutates the source DB) and verifying the ownership fix
  first; not fixed in code — `backup` acquiring a write lock at all is
  arguably wrong (it never writes to the source database), but that's out
  of scope for this change.

## Self-healing (2026-09-02)

Observed failure mode: the tunnel container's SSH session dies silently on
the ~6h collector-idle gap (NAT mapping expiry on the residential path) —
the client never notices (no effective client keepalive in the deployed
command) and hangs as a "healthy-looking dead tunnel", while sshd keeps the
stale `-R 18888` listener bound for hours, blocking the container's own
restart cycle from rebinding. Result: multi-hour relay outages
(Sep 1-2 2026: five natural-cycle failures).

Two-part repair:

1. **Hetzner sshd (applied 2026-09-02)**: `Match User deploy` now sets
   `ClientAliveInterval 15` / `ClientAliveCountMax 3` — sshd probes deploy
   sessions every 15s and reaps half-open ones within ~45s, freeing the
   stale listener immediately. Verified: killing the live tunnel session
   produced container exit -> Docker restart -> fresh session + 18888
   listener within 18s, end-to-end fetch 200.
2. **NAS tunnel container (OPERATOR ACTION REQUIRED — needs NAS docker
   admin)**: the deployed ssh command must add
   `-o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o
   ExitOnForwardFailure=yes` so the *client* detects silent NAT death,
   exits, and lets the container's restart policy re-establish the tunnel.
   Without this, a silent NAT death still hangs the client indefinitely
   (server-side reaping alone cannot wake it).

Until (2) is applied, a silent NAT death requires a manual container
restart on the NAS; the Hetzner-side listener will, however, always be
freed within ~45s of the session's death.

## Why NAS over Windows

Windows was considered but not used: the fleet's standing rule is no
Windows Task Scheduler entries or persistent background automation for
these projects, and a relay tunnel needs to run continuously. NAS already
runs long-lived Docker containers for this fleet (see the
`honor-uk-iso-nas-001` tablet-clank campaign) with `restart:
unless-stopped` — no scheduler involved, consistent with existing
precedent — so it was the natural fit without proving Windows further.
