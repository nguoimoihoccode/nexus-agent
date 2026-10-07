#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_ENV_FILE="$(mktemp)"
SECRETS_BASELINE="$(mktemp)"
TRIVY_IMAGE="${TRIVY_IMAGE:-aquasec/trivy:0.71.0}"
TRIVY_CACHE_VOLUME="${TRIVY_CACHE_VOLUME:-nexus-trivy-cache}"
HOST_CA_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
trap 'rm -f "$COMPOSE_ENV_FILE" "$SECRETS_BASELINE"' EXIT

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-nexus-prod-audit-password}"
export NEXUS_BACKEND_DB_USER="${NEXUS_BACKEND_DB_USER:-nexus_backend_audit}"
export NEXUS_BACKEND_DB_PASSWORD="${NEXUS_BACKEND_DB_PASSWORD:-nexus-backend-audit-password}"
export NEXUS_QUANT_DATA_DB_USER="${NEXUS_QUANT_DATA_DB_USER:-nexus_quant_data_audit}"
export NEXUS_QUANT_DATA_DB_PASSWORD="${NEXUS_QUANT_DATA_DB_PASSWORD:-nexus-qdata-audit-password}"
export NEXUS_QUANT_WORKER_DB_USER="${NEXUS_QUANT_WORKER_DB_USER:-nexus_quant_worker_audit}"
export NEXUS_QUANT_WORKER_DB_PASSWORD="${NEXUS_QUANT_WORKER_DB_PASSWORD:-nexus-qworker-audit-password}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-dummy-deepseek-key}"
export AI_TRADER_TOKEN="${AI_TRADER_TOKEN:-dummy-ai-trader-token}"
export NEXUS_OIDC_ISSUER="${NEXUS_OIDC_ISSUER:-https://identity.example.com}"
export NEXUS_OIDC_AUDIENCE="${NEXUS_OIDC_AUDIENCE:-nexus-api}"
export NEXUS_OIDC_CLIENT_ID="${NEXUS_OIDC_CLIENT_ID:-nexus-bff}"
export NEXUS_OIDC_CLIENT_SECRET="${NEXUS_OIDC_CLIENT_SECRET:-audit-only-oidc-client-secret-32chars}" # pragma: allowlist secret
export NEXUS_APP_ORIGIN="${NEXUS_APP_ORIGIN:-https://nexus.example.com}"
export NEXUS_BROWSER_SESSION_KEY="${NEXUS_BROWSER_SESSION_KEY:-0123456789abcdef0123456789abcdef}" # pragma: allowlist secret
export NEXUS_TENANT_HMAC_KEY="${NEXUS_TENANT_HMAC_KEY:-audit-only-tenant-hmac-key-32chars}"
export NEXUS_QUANT_WORKER_TOKEN="${NEXUS_QUANT_WORKER_TOKEN:-audit-only-quant-worker-token-32chars}"
export NEXUS_QUANT_DATA_WORKER_TOKEN="${NEXUS_QUANT_DATA_WORKER_TOKEN:-audit-only-quant-data-token-32chars}"
export LANGGRAPH_AES_KEY="${LANGGRAPH_AES_KEY:-0123456789abcdef0123456789abcdef}"
export LANGSMITH_TRACING="${LANGSMITH_TRACING:-false}"

audit_python_requirements() {
  local package_dir="$1"
  shift
  local requirements
  requirements="$(mktemp)"
  (
    cd "$ROOT_DIR/$package_dir"
    uv export --frozen --no-hashes "$@" > "$requirements"
    uvx --from pip-audit==2.10.1 pip-audit \
      --progress-spinner off --timeout 60 --disable-pip --no-deps -r "$requirements"
  )
  rm -f "$requirements"
}

cd "$ROOT_DIR"

docker compose \
  --env-file "$COMPOSE_ENV_FILE" \
  -f docker-compose.yml \
  -f docker-compose.beta.yml \
  config --quiet

run_trivy_fs() {
  local -a arguments=(
    --scanners vuln,secret,misconfig \
    --severity HIGH,CRITICAL \
    --exit-code 1 \
    --skip-dirs .git \
    --skip-dirs data \
    --skip-dirs postgres-data \
    --skip-dirs node_modules \
    --skip-dirs '**/.venv' \
    --skip-files '**/.env' \
    .
  )

  if command -v trivy >/dev/null 2>&1; then
    trivy fs "${arguments[@]}"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker run --rm \
      --env HTTP_PROXY \
      --env HTTPS_PROXY \
      --env NO_PROXY \
      --env http_proxy \
      --env https_proxy \
      --env no_proxy \
      --env SSL_CERT_FILE=/etc/ssl/certs/host-ca-certificates.crt \
      --volume "$ROOT_DIR:/workspace:ro" \
      --volume "$TRIVY_CACHE_VOLUME:/root/.cache/trivy" \
      --volume "$HOST_CA_CERT_FILE:/etc/ssl/certs/host-ca-certificates.crt:ro" \
      --workdir /workspace \
      "$TRIVY_IMAGE" fs "${arguments[@]}"
    return
  fi
  echo "trivy or docker is required for the product security gate" >&2
  return 1
}

run_trivy_fs

uvx --from bandit==1.9.4 bandit -q -r \
  backend/source \
  quant-worker/worker quant-data-worker/worker ai-trader-worker/worker

uvx --from semgrep==1.169.0 semgrep \
  --error \
  --no-git-ignore \
  --config=p/python \
  --config=p/javascript \
  --config=p/dockerfile \
  --exclude .venv \
  --exclude node_modules \
  --exclude dist \
  --exclude data \
  --exclude postgres-data \
  --exclude mlruns \
  --exclude catboost_info \
  backend/source \
  ai-trader-worker/worker quant-data-worker/worker quant-worker/worker \
  scripts/check-architecture.py scripts/run-product-validation.py \
  frontend/src frontend/e2e frontend/playwright.config.ts \
  backend/Dockerfile \
  ai-trader-worker/Dockerfile quant-data-worker/Dockerfile quant-worker/Dockerfile \
  frontend/Dockerfile frontend/nginx.conf

cp .secrets.baseline "$SECRETS_BASELINE"
git ls-files --cached --others --exclude-standard -z \
  | while IFS= read -r -d '' file; do
      [[ "$file" == ".secrets.baseline" ]] || printf '%s\0' "$file"
    done \
  | xargs -0 uvx --from detect-secrets==1.5.0 detect-secrets-hook \
      --baseline "$SECRETS_BASELINE"

(
  cd frontend
  npm audit --audit-level=high
)

audit_python_requirements backend --extra dev
audit_python_requirements ai-trader-worker --extra dev
audit_python_requirements quant-data-worker --extra dev --extra openbb
audit_python_requirements quant-worker --extra dev --extra qlib
