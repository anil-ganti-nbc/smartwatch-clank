#!/bin/sh
# Feature Phone-style production cron wrapper for Smartwatch Clank.
# Canonical tracked source of the host-side /home/deploy/staging/smartwatch-clank/deploy_run.sh
# (untracked deployment artefact). Keep the two in sync: the host copy is
# what the deploy-user crontab (`50 1-23/2 * * *`) executes.
#
# Usage: deploy_run.sh   # runs the compose service default: run --mode production
set -eu
cd "$(dirname "$0")"
export IMAGE_TAG
IMAGE_TAG="$(cat .deployed-id)"

# Garmin-only egress relay (see docs/garmin-egress-relay.md and
# docs/ticket-garmin-relay-production-wiring.md): identical canonical rule
# to deploy/run.sh. ANY lane capable of executing a www.garmin.com collector
# must provide the relay unless explicitly disabled with
# SMARTWATCH_CLANK_GARMIN_PROXY="" -- direct www.garmin.com retrieval is
# Cloudflare-blocked from this host. `${VAR-default}` (not `${VAR:-default}`)
# so an explicit empty disable is honoured.
export SMARTWATCH_CLANK_GARMIN_PROXY="${SMARTWATCH_CLANK_GARMIN_PROXY-http://host.docker.internal:18889}"

exec docker compose -f docker-compose.staging.yml run --rm smartwatch-clank
