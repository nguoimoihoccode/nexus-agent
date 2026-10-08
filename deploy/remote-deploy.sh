#!/usr/bin/env bash
#
# Runs ON the host, not locally. The deploy workflow pipes this file over ssh
# and passes the image tag as the first argument.
#
#   ssh deploy@host 'bash -s -- sha-a1b2c3d' < deploy/remote-deploy.sh
#
# It is idempotent: running it twice with the same tag only pulls and restarts.
# Running it with an older tag is the rollback path.
#
# Environment overrides:
#   NEXUS_APP_DIR          checkout location (default /opt/nexus/nexus-agent)
#   NEXUS_ENV_SOURCE       staged .env written by the caller (default
#                          $HOME/nexus-demo.env)
#   NEXUS_PUBLIC_URL       health-check target (default the demo domain)

set -euo pipefail

TAG="${1:?usage: remote-deploy.sh <image-tag>}"
APP_DIR="${NEXUS_APP_DIR:-/opt/nexus/nexus-agent}"
ENV_FILE="${APP_DIR}/.env"
ENV_SOURCE="${NEXUS_ENV_SOURCE:-${HOME}/nexus-demo.env}"
PUBLIC_URL="${NEXUS_PUBLIC_URL:-https://nguoimoihoccode.io.vn/}"

# Services this overlay deploys, so `pull` never depends on profile resolution.
# Keep in step with deploy/publish-image-*.yml and docker-compose.deploy.yml.
SERVICES=(
  postgres
  domain-migrate
  backend
  ai-trader-worker
  ai-trader-worker-refresh
  frontend
)

cd "$APP_DIR"

# 1. Take the staged environment file if the caller wrote one, then pin the tag.
#    The staged file is written over ssh with mode 600 and removed after use so
#    secret material does not linger in the home directory.
if [ -f "$ENV_SOURCE" ]; then
  install -m 600 "$ENV_SOURCE" "$ENV_FILE"
  rm -f "$ENV_SOURCE"
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "missing ${ENV_FILE}: run deploy/setup-vps.sh and stage the environment first" >&2
  exit 1
fi
chmod 600 "$ENV_FILE"
if grep -qE '^NEXUS_IMAGE_TAG=' "$ENV_FILE"; then
  sed -i "s|^NEXUS_IMAGE_TAG=.*|NEXUS_IMAGE_TAG=${TAG}|" "$ENV_FILE"
else
  printf 'NEXUS_IMAGE_TAG=%s\n' "$TAG" >> "$ENV_FILE"
fi

# 2. The checkout is a deploy target, so it is forced to match the remote. Any
#    local edit here is discarded on purpose; make changes through git.
git fetch --prune origin main
git reset --hard origin/main

# 3. Pull and restart. Published images are public, so the host needs no
#    registry credential.
COMPOSE=(
  docker compose
  -f docker-compose.yml
  -f docker-compose.demo.yml
  -f docker-compose.deploy.yml
  --env-file "$ENV_FILE"
)

"${COMPOSE[@]}" pull "${SERVICES[@]}"
"${COMPOSE[@]}" up -d --remove-orphans

# 4. Drop images that are neither running nor recent, so repeated deploys do not
#    fill the disk. A week of history is kept so a rollback still finds its tag.
docker image prune -f --filter "until=168h" >/dev/null

echo "deployed ${TAG}"
echo "public endpoint: ${PUBLIC_URL}"