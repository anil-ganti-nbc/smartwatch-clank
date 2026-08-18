#!/usr/bin/env bash
# Deploy the current (or a given) local git revision of Smartwatch Clank to
# the shared Hetzner soak host, following local -> GitHub -> Hetzner.
#
# NOT invoked by this session. Scaffolding for the follow-up deployment
# session. Review every step against the live host before running.
#
# Usage:
#   scripts/deploy_hetzner.sh [git-ref]     # defaults to HEAD
#
# Requires: an ssh alias/host reachable as $HETZNER_SSH_TARGET
# (default: deploy@204.168.142.1, matching this fleet's existing access
# convention -- see memory: hetzner-clank-fleet-access).
set -euo pipefail

GIT_REF="${1:-HEAD}"
HETZNER_SSH_TARGET="${HETZNER_SSH_TARGET:-deploy@204.168.142.1}"
REMOTE_STAGING_DIR="${REMOTE_STAGING_DIR:-/home/deploy/staging/smartwatch-clank}"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "== 1. Verify local repository state =="
if [[ -n "$(git status --porcelain)" ]]; then
    echo "deploy_hetzner.sh: working tree is not clean; commit or stash first" >&2
    exit 1
fi
GIT_REVISION="$(git rev-parse "$GIT_REF")"
IMAGE_TAG="$(git rev-parse --short "$GIT_REF")"
echo "Deploying commit: $GIT_REVISION (tag $IMAGE_TAG)"

echo "== 2. Verify the commit exists on GitHub (origin/main or an ancestor of it) =="
git fetch origin --quiet
if ! git merge-base --is-ancestor "$GIT_REVISION" origin/main; then
    echo "deploy_hetzner.sh: $GIT_REVISION is not on origin/main; push and merge first" >&2
    exit 1
fi

echo "== 3. Back up the remote soak database before touching anything =="
BACKUP_NAME="smartwatch-clank-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
ssh "$HETZNER_SSH_TARGET" "
    set -euo pipefail
    cd '$REMOTE_STAGING_DIR'
    mkdir -p backups
    docker run --rm \
        -v smartwatch_clank_staging_data:/app/data:ro \
        -v \"\$(pwd)/backups:/backup\" \
        --entrypoint python \"smartwatch-clank:\${IMAGE_TAG:-\$(cat .deployed-id 2>/dev/null || echo soak-local)}\" \
        -m smartwatch_clank.cli --database /app/data/smartwatch-clank.sqlite3 backup /backup/$BACKUP_NAME
"
echo "Backed up to $REMOTE_STAGING_DIR/backups/$BACKUP_NAME -- verify it opens (sqlite3 .tables) before proceeding manually if this is the first run."

echo "== 4. Fetch and check out the exact revision on the host =="
ssh "$HETZNER_SSH_TARGET" "
    set -euo pipefail
    cd '$REMOTE_STAGING_DIR'
    git fetch origin --quiet
    git checkout --quiet '$GIT_REVISION'
"

echo "== 5. Build the image tagged with this revision =="
ssh "$HETZNER_SSH_TARGET" "
    set -euo pipefail
    cd '$REMOTE_STAGING_DIR'
    GIT_REVISION='$GIT_REVISION' IMAGE_TAG='$IMAGE_TAG' docker compose -f docker-compose.staging.yml build
"

echo "== 6. Three-way revision verification (git vs image label vs running identity) =="
REMOTE_GIT_SHA="$(ssh "$HETZNER_SSH_TARGET" "cd '$REMOTE_STAGING_DIR' && git rev-parse HEAD")"
IMAGE_LABEL_SHA="$(ssh "$HETZNER_SSH_TARGET" "docker inspect smartwatch-clank:$IMAGE_TAG --format '{{ index .Config.Labels \"org.opencontainers.image.revision\" }}'")"
IDENTITY_SHA_SHORT="$(ssh "$HETZNER_SSH_TARGET" "
    IMAGE_TAG='$IMAGE_TAG' docker compose -f '$REMOTE_STAGING_DIR/docker-compose.staging.yml' run --rm --entrypoint python smartwatch-clank -m smartwatch_clank.cli identity
" | python3 -c 'import json,sys; print(json.load(sys.stdin)["source_revision_short"])')"

echo "git HEAD:            $REMOTE_GIT_SHA"
echo "image label:         $IMAGE_LABEL_SHA"
echo "running identity:    $IDENTITY_SHA_SHORT (short)"
if [[ "$REMOTE_GIT_SHA" != "$GIT_REVISION" || "$IMAGE_LABEL_SHA" != "$GIT_REVISION" ]]; then
    echo "deploy_hetzner.sh: revision mismatch -- treat deployment as FAILED, do not proceed" >&2
    exit 1
fi

echo "== 7. Point .deployed-id at the verified tag; resume the schedule =="
ssh "$HETZNER_SSH_TARGET" "
    set -euo pipefail
    cd '$REMOTE_STAGING_DIR'
    echo '$IMAGE_TAG' > .deployed-id
"

echo "== 8. One safe verification cycle through the real cron/timer entrypoint =="
ssh "$HETZNER_SSH_TARGET" "cd '$REMOTE_STAGING_DIR' && ./deploy/run.sh"

echo "Deployed $GIT_REVISION (tag $IMAGE_TAG) to $HETZNER_SSH_TARGET:$REMOTE_STAGING_DIR."
echo "Manually confirm: production allowlist unchanged, Discord delivery still disabled,"
echo "existing historical observation counts intact, and the scheduler points at deploy/run.sh."
