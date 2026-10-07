#!/usr/bin/env bash

set -Eeuo pipefail

nexus_append_no_proxy() {
  local value="${1:-}"
  shift || true

  local host
  for host in "$@"; do
    case ",${value}," in
      *",${host},"*) ;;
      *) value="${value:+${value},}${host}" ;;
    esac
  done

  printf '%s\n' "$value"
}

main() {
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
QUANT_DIR="$ROOT_DIR/quant-worker"
QUANT_DATA_WORKER_DIR="$ROOT_DIR/quant-data-worker"
AI_TRADER_WORKER_DIR="$ROOT_DIR/ai-trader-worker"
AI_TRADER_WORKER_ENV_FILE="$AI_TRADER_WORKER_DIR/.env"
NEXUS_DATA_DIR="${NEXUS_DATA_DIR:-$ROOT_DIR/data}"
QUANT_DATA_DIR="$NEXUS_DATA_DIR/qlib"
QUANT_ARTIFACTS_DIR="$NEXUS_DATA_DIR/artifacts"
QUANT_DATA_STAGING_DIR="$NEXUS_DATA_DIR/staging"
AI_TRADER_DATA_DIR="$NEXUS_DATA_DIR/ai-trader"
NEXUS_LOG_DIR="${NEXUS_LOG_DIR:-$NEXUS_DATA_DIR/logs}"
LOG_SESSION_DIR="$NEXUS_LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$$"

export NEXUS_ENV="${NEXUS_ENV:-development}"
export NEXUS_LOG_LEVEL="${NEXUS_LOG_LEVEL:-INFO}"
export NEXUS_LOG_FORMAT="${NEXUS_LOG_FORMAT:-json}"

prefix_service_log() {
  local service="$1"
  local log_file="$2"
  while IFS= read -r line || [[ -n "$line" ]]; do
    printf '[%s] %s\n' "$service" "$line"
  done | tee -a "$log_file"
}

if [[ -x "$BACKEND_DIR/.venv/bin/langgraph" ]]; then
  LANGGRAPH_BIN="$BACKEND_DIR/.venv/bin/langgraph"
elif [[ -x "$ROOT_DIR/venv/bin/langgraph" ]]; then
  LANGGRAPH_BIN="$ROOT_DIR/venv/bin/langgraph"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/langgraph" ]]; then
  LANGGRAPH_BIN="$VIRTUAL_ENV/bin/langgraph"
else
  echo "Không tìm thấy langgraph CLI. Chạy: cd backend && uv sync --extra dev" >&2
  exit 1
fi

SCRIPT_PYTHON="$(dirname -- "$LANGGRAPH_BIN")/python"
if [[ ! -x "$SCRIPT_PYTHON" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    SCRIPT_PYTHON="$(command -v python3)"
  else
    echo "Không tìm thấy Python interpreter cho launcher." >&2
    exit 1
  fi
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Không tìm thấy npm trong PATH." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Không tìm thấy uv trong PATH." >&2
  exit 1
fi

case "${NEXUS_BACKEND_RELOAD:-0}" in
  0) backend_reload_args=(--no-reload) ;;
  1) backend_reload_args=() ;;
  *)
    echo "NEXUS_BACKEND_RELOAD chỉ chấp nhận 0 hoặc 1." >&2
    exit 1
    ;;
esac

backend_no_proxy="$(nexus_append_no_proxy \
  "${NO_PROXY:-${no_proxy:-}}" localhost 127.0.0.1 ::1)"

dotenv_value() {
  local env_file="$1"
  local key="$2"
  [[ -f "$env_file" ]] || return 0
  "$SCRIPT_PYTHON" - "$env_file" "$key" <<'PY'
import sys

path, key = sys.argv[1], sys.argv[2]
try:
    lines = open(path, encoding="utf-8").read().splitlines()
except OSError:
    sys.exit(0)

for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    name, value = stripped.split("=", 1)
    name = name.strip()
    if name.startswith("export "):
        name = name[7:].strip()
    if name != key:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    print(value)
    break
PY
}

tcp_open() {
  "$SCRIPT_PYTHON" - "$1" "$2" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=1.5):
        pass
except OSError:
    sys.exit(1)
PY
}

postgres_host="${POSTGRES_HOST:-$(dotenv_value "$BACKEND_DIR/.env" POSTGRES_HOST)}"
postgres_host="${postgres_host:-127.0.0.1}"
postgres_port="${POSTGRES_PORT:-$(dotenv_value "$BACKEND_DIR/.env" POSTGRES_PORT)}"
postgres_port="${postgres_port:-5433}"

if ! tcp_open "$postgres_host" "$postgres_port"; then
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "PostgreSQL không reachable tại ${postgres_host}:${postgres_port}; thử bật Docker Compose postgres..." >&2
    if docker compose --env-file "$BACKEND_DIR/.env" up -d postgres >/dev/null 2>&1; then
      postgres_container="$(docker compose --env-file "$BACKEND_DIR/.env" ps -q postgres 2>/dev/null || true)"
      postgres_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$postgres_container" 2>/dev/null || true)"
      if [[ -n "$postgres_ip" ]] && tcp_open "$postgres_ip" 5432; then
        postgres_host="$postgres_ip"
        postgres_port=5432
        echo "Dùng PostgreSQL Docker tại ${postgres_host}:${postgres_port} cho backend local." >&2
      fi
    fi
  fi
fi

if ! tcp_open "$postgres_host" "$postgres_port"; then
  echo "Không kết nối được PostgreSQL tại ${postgres_host}:${postgres_port}." >&2
  echo "Bật Docker Compose postgres hoặc cấu hình POSTGRES_HOST/POSTGRES_PORT rồi chạy lại ./run.sh." >&2
  exit 1
fi

export POSTGRES_HOST="$postgres_host"
export POSTGRES_PORT="$postgres_port"

if [[ "${NEXUS_ENV}" != "production" && "${NEXUS_AUTO_MIGRATE:-1}" != "0" ]]; then
  echo "Kiểm tra và nâng domain schema migrations local..."
  if ! (
    cd "$BACKEND_DIR"
    uv run --no-project --env-file .env -- "$SCRIPT_PYTHON" -m source.infrastructure.migrations upgrade
  ); then
    echo "Domain schema migration thất bại; dừng trước khi khởi động backend." >&2
    echo "Sửa database hoặc chạy lại với NEXUS_AUTO_MIGRATE=0 để xử lý thủ công." >&2
    exit 1
  fi
fi

for required_port in 2024 5173 8000 8001 8100; do
  if tcp_open 127.0.0.1 "$required_port"; then
    echo "Port ${required_port} đang được sử dụng; có thể một phiên ./run.sh khác vẫn đang chạy." >&2
    echo "Dừng phiên cũ bằng Ctrl+C rồi chạy lại ./run.sh; không khởi động stack trùng." >&2
    exit 1
  fi
done

QUANT_WORKER_CMD=()
if [[ -x "$QUANT_DIR/.venv/bin/uvicorn" ]]; then
  QUANT_WORKER_CMD=("$QUANT_DIR/.venv/bin/uvicorn")
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]] \
  && "$VIRTUAL_ENV/bin/python" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  QUANT_WORKER_CMD=("$VIRTUAL_ENV/bin/python" "-m" "uvicorn")
  echo "Không thấy quant-worker/.venv; dùng tạm active venv cho Quant Worker." >&2
else
  echo "Bỏ qua Quant Worker local: thiếu runtime. Chạy API nhẹ: cd quant-worker && uv sync" >&2
  echo "Khi cần chạy Qlib experiment thật: cd quant-worker && uv sync --extra qlib" >&2
fi

QUANT_DATA_WORKER_PYTHON=""
if [[ -x "$QUANT_DATA_WORKER_DIR/.venv/bin/python" ]] \
  && "$QUANT_DATA_WORKER_DIR/.venv/bin/python" -c 'import fastapi, uvicorn, numpy, pandas' >/dev/null 2>&1; then
  QUANT_DATA_WORKER_PYTHON="$QUANT_DATA_WORKER_DIR/.venv/bin/python"
elif [[ -x "$QUANT_DIR/.venv/bin/python" ]] \
  && "$QUANT_DIR/.venv/bin/python" -c 'import fastapi, uvicorn, numpy, pandas' >/dev/null 2>&1; then
  QUANT_DATA_WORKER_PYTHON="$QUANT_DIR/.venv/bin/python"
  echo "Không thấy quant-data-worker/.venv; dùng tạm quant-worker/.venv cho Quant Data Worker." >&2
  echo "Muốn fetch OpenBB thật, chạy: cd quant-data-worker && uv sync --extra openbb" >&2
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]] \
  && "$VIRTUAL_ENV/bin/python" -c 'import fastapi, uvicorn, numpy, pandas' >/dev/null 2>&1; then
  QUANT_DATA_WORKER_PYTHON="$VIRTUAL_ENV/bin/python"
  echo "Không thấy quant-data-worker/.venv; dùng tạm active venv cho Quant Data Worker." >&2
else
  echo "Bỏ qua Quant Data Worker local: thiếu runtime. Khi cần ingest data thật, chạy: cd quant-data-worker && uv sync" >&2
fi

AI_TRADER_WORKER_PYTHON=""
if [[ -x "$AI_TRADER_WORKER_DIR/.venv/bin/python" ]] \
  && "$AI_TRADER_WORKER_DIR/.venv/bin/python" -c 'import fastapi, uvicorn, pydantic' >/dev/null 2>&1; then
  AI_TRADER_WORKER_PYTHON="$AI_TRADER_WORKER_DIR/.venv/bin/python"
else
  echo "Bỏ qua AI-Trader Worker local: thiếu runtime. Chạy: cd ai-trader-worker && uv sync --extra dev" >&2
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "Thiếu frontend/node_modules. Chạy: cd frontend && npm install" >&2
  exit 1
fi

backend_pid=""
frontend_pid=""
quant_pid=""
quant_data_pid=""
ai_trader_pid=""
ai_trader_refresh_pid=""
pids=()

cleanup() {
  trap - EXIT INT TERM

  for pid in "${pids[@]}"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM -- "-$pid" 2>/dev/null || true
    fi
  done

  if ((${#pids[@]})); then
    wait "${pids[@]}" 2>/dev/null || true
  fi
}

trap cleanup EXIT
trap 'exit 130' INT TERM

mkdir -p \
  "$QUANT_DATA_DIR" \
  "$QUANT_ARTIFACTS_DIR" \
  "$QUANT_DATA_STAGING_DIR" \
  "$AI_TRADER_DATA_DIR" \
  "$LOG_SESSION_DIR"
echo "Log theo service: $LOG_SESSION_DIR"

if ((${#QUANT_WORKER_CMD[@]})); then
  echo "Khởi động Quant Worker: http://127.0.0.1:8000"
  setsid bash -c '
    cd "$1"
    export NEXUS_DATA_DIR="$2"
    export QLIB_DATA_DIR="$3"
    export QUANT_ARTIFACTS_DIR="$4"
    shift 4
    exec "$@" worker.app:app --host 127.0.0.1 --port 8000 --no-access-log
  ' \
    _ "$QUANT_DIR" "$NEXUS_DATA_DIR" "$QUANT_DATA_DIR" "$QUANT_ARTIFACTS_DIR" "${QUANT_WORKER_CMD[@]}" \
    > >(prefix_service_log "quant-worker" "$LOG_SESSION_DIR/quant-worker.log") 2>&1 &
  quant_pid=$!
  pids+=("$quant_pid")
fi

if [[ -n "$QUANT_DATA_WORKER_PYTHON" ]]; then
  echo "Khởi động Quant Data Worker: http://127.0.0.1:8001"
  setsid bash -c '
    cd "$1"
    export NEXUS_DATA_DIR="$2"
    export QUANT_DATA_WORKER_DATA_DIR="$3"
    export QUANT_DATA_WORKER_STAGING_DIR="$4"
    exec "$5" -m uvicorn worker.app:app --host 127.0.0.1 --port 8001 --no-access-log
  ' \
    _ "$QUANT_DATA_WORKER_DIR" "$NEXUS_DATA_DIR" "$QUANT_DATA_DIR" "$QUANT_DATA_STAGING_DIR" "$QUANT_DATA_WORKER_PYTHON" \
    > >(prefix_service_log "quant-data-worker" "$LOG_SESSION_DIR/quant-data-worker.log") 2>&1 &
  quant_data_pid=$!
  pids+=("$quant_data_pid")
fi

backend_ai_trader_url="${AI_TRADER_API_BASE_URL:-}"
if [[ -n "$AI_TRADER_WORKER_PYTHON" ]]; then
  echo "Khởi động AI-Trader Worker: http://127.0.0.1:8100"
  backend_ai_trader_url="${backend_ai_trader_url:-http://127.0.0.1:8100/api}"
  ai_trader_token="${AI_TRADER_TOKEN:-}"
  if [[ -z "$ai_trader_token" ]]; then
    ai_trader_token="$(dotenv_value "$AI_TRADER_WORKER_ENV_FILE" AI_TRADER_TOKEN)"
  fi
  alpha_vantage_api_key="${ALPHA_VANTAGE_API_KEY:-}"
  alpha_vantage_base_url="${ALPHA_VANTAGE_BASE_URL:-}"
  market_news_lookback_hours="${AI_TRADER_MARKET_NEWS_LOOKBACK_HOURS:-}"
  market_news_limit="${AI_TRADER_MARKET_NEWS_LIMIT:-}"
  market_news_categories="${AI_TRADER_MARKET_NEWS_CATEGORIES:-}"
  market_news_refresh_interval_seconds="${AI_TRADER_MARKET_NEWS_REFRESH_INTERVAL_SECONDS:-}"
  market_news_refresh_startup_delay_seconds="${AI_TRADER_MARKET_NEWS_REFRESH_STARTUP_DELAY_SECONDS:-}"
  market_news_stale_after_seconds="${AI_TRADER_MARKET_NEWS_STALE_AFTER_SECONDS:-}"
  if [[ -z "$alpha_vantage_api_key" ]]; then
    alpha_vantage_api_key="$(dotenv_value "$AI_TRADER_WORKER_ENV_FILE" ALPHA_VANTAGE_API_KEY)"
  fi
  if [[ -z "$alpha_vantage_base_url" ]]; then
    alpha_vantage_base_url="$(dotenv_value "$AI_TRADER_WORKER_ENV_FILE" ALPHA_VANTAGE_BASE_URL)"
  fi
  if [[ -z "$market_news_lookback_hours" ]]; then
    market_news_lookback_hours="$(dotenv_value "$AI_TRADER_WORKER_ENV_FILE" AI_TRADER_MARKET_NEWS_LOOKBACK_HOURS)"
  fi
  if [[ -z "$market_news_limit" ]]; then
    market_news_limit="$(dotenv_value "$AI_TRADER_WORKER_ENV_FILE" AI_TRADER_MARKET_NEWS_LIMIT)"
  fi
  if [[ -z "$market_news_categories" ]]; then
    market_news_categories="$(dotenv_value "$AI_TRADER_WORKER_ENV_FILE" AI_TRADER_MARKET_NEWS_CATEGORIES)"
  fi
  if [[ -z "$market_news_refresh_interval_seconds" ]]; then
    market_news_refresh_interval_seconds="$(dotenv_value "$AI_TRADER_WORKER_ENV_FILE" AI_TRADER_MARKET_NEWS_REFRESH_INTERVAL_SECONDS)"
  fi
  if [[ -z "$market_news_refresh_startup_delay_seconds" ]]; then
    market_news_refresh_startup_delay_seconds="$(dotenv_value "$AI_TRADER_WORKER_ENV_FILE" AI_TRADER_MARKET_NEWS_REFRESH_STARTUP_DELAY_SECONDS)"
  fi
  if [[ -z "$market_news_stale_after_seconds" ]]; then
    market_news_stale_after_seconds="$(dotenv_value "$AI_TRADER_WORKER_ENV_FILE" AI_TRADER_MARKET_NEWS_STALE_AFTER_SECONDS)"
  fi
  AI_TRADER_TOKEN="$ai_trader_token" \
  ALPHA_VANTAGE_API_KEY="$alpha_vantage_api_key" \
  ALPHA_VANTAGE_BASE_URL="$alpha_vantage_base_url" \
  AI_TRADER_MARKET_NEWS_LOOKBACK_HOURS="$market_news_lookback_hours" \
  AI_TRADER_MARKET_NEWS_LIMIT="$market_news_limit" \
  AI_TRADER_MARKET_NEWS_CATEGORIES="$market_news_categories" \
  AI_TRADER_MARKET_NEWS_REFRESH_INTERVAL_SECONDS="$market_news_refresh_interval_seconds" \
  AI_TRADER_MARKET_NEWS_REFRESH_STARTUP_DELAY_SECONDS="$market_news_refresh_startup_delay_seconds" \
  AI_TRADER_MARKET_NEWS_STALE_AFTER_SECONDS="$market_news_stale_after_seconds" \
  setsid bash -c '
    cd "$1"
    export DB_PATH="$2/clawtrader.db"
    exec "$3" -m uvicorn worker.app:app --host 127.0.0.1 --port 8100 --no-access-log
  ' \
    _ "$AI_TRADER_WORKER_DIR" "$AI_TRADER_DATA_DIR" "$AI_TRADER_WORKER_PYTHON" \
    > >(prefix_service_log "ai-trader-worker" "$LOG_SESSION_DIR/ai-trader-worker.log") 2>&1 &
  ai_trader_pid=$!
  pids+=("$ai_trader_pid")

  echo "Khởi động AI-Trader Market Refresh Loop"
  AI_TRADER_TOKEN="$ai_trader_token" \
  ALPHA_VANTAGE_API_KEY="$alpha_vantage_api_key" \
  ALPHA_VANTAGE_BASE_URL="$alpha_vantage_base_url" \
  AI_TRADER_MARKET_NEWS_LOOKBACK_HOURS="$market_news_lookback_hours" \
  AI_TRADER_MARKET_NEWS_LIMIT="$market_news_limit" \
  AI_TRADER_MARKET_NEWS_CATEGORIES="$market_news_categories" \
  AI_TRADER_MARKET_NEWS_REFRESH_INTERVAL_SECONDS="$market_news_refresh_interval_seconds" \
  AI_TRADER_MARKET_NEWS_REFRESH_STARTUP_DELAY_SECONDS="$market_news_refresh_startup_delay_seconds" \
  AI_TRADER_MARKET_NEWS_STALE_AFTER_SECONDS="$market_news_stale_after_seconds" \
  setsid bash -c '
    cd "$1"
    export DB_PATH="$2/clawtrader.db"
    exec "$3" -m worker.daemon
  ' \
    _ "$AI_TRADER_WORKER_DIR" "$AI_TRADER_DATA_DIR" "$AI_TRADER_WORKER_PYTHON" \
    > >(prefix_service_log "ai-trader-refresh" "$LOG_SESSION_DIR/ai-trader-refresh.log") 2>&1 &
  ai_trader_refresh_pid=$!
  pids+=("$ai_trader_refresh_pid")
fi

echo "Khởi động BE: http://127.0.0.1:2024"
setsid bash -c '
  backend_dir="$1"
  langgraph_bin="$2"
  ai_trader_url="$3"
  backend_no_proxy="$4"
  shift 4

  cd "$backend_dir"
  if [[ -n "$ai_trader_url" ]]; then
    export AI_TRADER_API_BASE_URL="$ai_trader_url"
  fi
  export NO_PROXY="$backend_no_proxy"
  export no_proxy="$backend_no_proxy"
  exec env \
    QUANT_WORKER_URL=http://127.0.0.1:8000 \
    QUANT_DATA_WORKER_URL=http://127.0.0.1:8001 \
    uv run --no-project --env-file .env -- "$langgraph_bin" dev --no-browser "$@" --allow-blocking --port 2024
' \
  _ "$BACKEND_DIR" "$LANGGRAPH_BIN" "$backend_ai_trader_url" "$backend_no_proxy" "${backend_reload_args[@]}" \
  > >(prefix_service_log "backend" "$LOG_SESSION_DIR/backend.log") 2>&1 &
backend_pid=$!
pids+=("$backend_pid")

echo "Khởi động FE: http://localhost:5173"
setsid bash -c 'cd "$1" && exec npm run dev' \
  _ "$FRONTEND_DIR" \
  > >(prefix_service_log "frontend" "$LOG_SESSION_DIR/frontend.log") 2>&1 &
frontend_pid=$!
pids+=("$frontend_pid")

echo "Nhấn Ctrl+C để dừng các service đang chạy."

if wait -n "${pids[@]}"; then
  exit_code=0
else
  exit_code=$?
fi

echo "Một service đã dừng; đang tắt service còn lại."
exit "$exit_code"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
