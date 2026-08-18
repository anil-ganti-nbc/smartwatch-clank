#!/usr/bin/env bash
# Host-side wrapper invoked by cron/systemd on the Hetzner soak host.
# Lives at /home/deploy/staging/smartwatch-clank/deploy/run.sh once deployed
# (this copy in the repo is the template deploy_hetzner.sh installs).
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

exec docker compose -f docker-compose.staging.yml run --rm \
    -e SMARTWATCH_CLANK_HOST_ID="${SMARTWATCH_CLANK_HOST_ID:-hetzner-clank-fleet-01}" \
    smartwatch-clank
