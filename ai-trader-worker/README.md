# Nexus AI-Trader Worker

This is a small Nexus-owned adapter worker for the `ai-trader-agent`. It keeps
the HTTP contract Nexus needs without running the full upstream AI-Trader
platform.

The worker is snapshot-only for market intelligence reads. A background refresh
loop periodically fetches Alpha Vantage `NEWS_SENTIMENT` and writes SQLite
snapshots; agent read tools continue to read SQLite only. If no local snapshot
exists, read endpoints return stable unavailable payloads.

## Local Setup

```bash
cd ai-trader-worker
cp .env.example .env
uv sync --extra dev
```

Run the worker manually:

```bash
uv run --env-file .env uvicorn worker.app:app --host 127.0.0.1 --port 8100
```

Run the refresh loop manually:

```bash
uv run --env-file .env python -m worker.daemon
```

Nexus `run.sh` starts this worker at `http://127.0.0.1:8100` when
`ai-trader-worker/.venv` is available and points the backend connector at
`http://127.0.0.1:8100/api`. It also starts the refresh loop as a sibling
process. `run.sh` reads worker settings from `ai-trader-worker/.env`.

## API Surface

- `GET /health`
- `GET /ready` (protected dependency readiness)
- `GET /api/market-intel/overview`
- `GET /api/market-intel/news` with optional `category`, `limit`, and
  comma-separated `symbols` query filters
- `POST /api/market-intel/refresh`
- `GET /api/signals/feed`
- `POST /api/signals/strategy`
- `POST /api/signals/discussion`
- `POST /api/claw/agents/heartbeat`

Read-only endpoints do not require auth. Refresh, publish, and heartbeat
endpoints require `AI_TRADER_TOKEN` through either
`Authorization: Bearer <token>` or `X-Claw-Token: <token>`.

Manual refresh example:

```bash
curl -X POST http://127.0.0.1:8100/api/market-intel/refresh \
  -H "X-Claw-Token: $AI_TRADER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"categories":["equities","macro","crypto","commodities"],"limit":50}'
```

Refresh requires `ALPHA_VANTAGE_API_KEY`. Production requires an HTTPS provider URL
whose host appears in `ALPHA_VANTAGE_ALLOWED_HOSTS` (default:
`www.alphavantage.co`). Optional knobs are `ALPHA_VANTAGE_BASE_URL`,
`ALPHA_VANTAGE_ALLOWED_HOSTS`, `AI_TRADER_MARKET_NEWS_LOOKBACK_HOURS`,
`AI_TRADER_MARKET_NEWS_LIMIT`, `AI_TRADER_MARKET_NEWS_CATEGORIES`,
`AI_TRADER_MARKET_NEWS_REFRESH_INTERVAL_SECONDS`,
`AI_TRADER_MARKET_NEWS_REFRESH_STARTUP_DELAY_SECONDS`, and
`AI_TRADER_MARKET_NEWS_STALE_AFTER_SECONDS`.

## Data

The worker uses SQLite from the Python standard library. It creates namespaced
tables in the configured database:

- `nexus_signals`
- `nexus_market_snapshots`

Existing upstream AI-Trader tables are ignored and are not migrated.
