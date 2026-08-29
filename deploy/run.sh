#!/usr/bin/env bash
# Host-side wrapper invoked by the smartwatch-clank-soak systemd timer on
# the Hetzner soak host. Lives at
# /home/deploy/staging/smartwatch-clank/deploy/run.sh once deployed (this
# copy in the repo is the template scripts/deploy_hetzner.sh installs).
#
# This is the EXPERIMENTAL soak: EXPERIMENTAL-tier collectors only (see
# RunScope in core/models.py). It intentionally does NOT run the
# PRODUCTION-tier Samsung collectors -- those run exclusively through the
# existing host-side deploy_run.sh (outside version control, not this
# file), which separately drives the production cron with the compose
# file's own default `run --mode production` command. Explicitly
# overriding to `--mode experimental` here, rather than relying on the
# compose default, is what keeps the two paths from silently becoming the
# same thing after a compose-file edit.
#
# (Before the RunScope fix, `--mode experimental` meant "every registered
# collector regardless of tier" -- an earlier version of this comment
# described that behavior. That was the bug: it ran the four production
# Samsung collectors through this soak too, alongside the production cron.
# This script's invocation didn't need to change, only what
# `--mode experimental` means underneath it.)
#
# No collection or reconciliation logic belongs here — this only launches
# the already-built image the same way for every cycle. Reads
# .deployed-id (written by scripts/deploy_hetzner.sh) so a schedule tick
# always runs the last verified revision, not whatever the working tree
# happens to contain.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -f .deployed-id ]]; then
    echo "run.sh: no .deployed-id present; deploy_hetzner.sh has not verified a revision yet" >&2
    exit 1
fi
export IMAGE_TAG="$(cat .deployed-id)"

# Garmin-only egress relay (see docs/garmin-egress-relay.md): 18888 is the
# fixed Hetzner-side loopback port the NAS relay tunnel binds via `ssh -R`.
# Set SMARTWATCH_CLANK_GARMIN_PROXY="" in the environment to disable it and
# fall back to direct (i.e. known-blocked) fetches for those 2 collectors.
exec docker compose -f docker-compose.staging.yml run --rm \
    -e SMARTWATCH_CLANK_HOST_ID="${SMARTWATCH_CLANK_HOST_ID:-hetzner-clank-fleet-01}" \
    -e SMARTWATCH_CLANK_GARMIN_PROXY="${SMARTWATCH_CLANK_GARMIN_PROXY-http://host.docker.internal:18888}" \
    smartwatch-clank run --mode experimental
