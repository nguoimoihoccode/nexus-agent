# AI-Trader Workflow

`ai-trader-worker/` is a small Nexus-owned adapter, not the upstream AI-Trader
platform. The backend communicates through
`backend/source/integrations/ai_trader_client.py`; it never imports worker modules.

```text
Alpha Vantage NEWS_SENTIMENT
  -> background refresh loop
  -> Nexus-owned SQLite snapshots
  -> read-only AI-Trader tools

Supervisor -> ai-trader-agent -> governed approval
  -> governed publication service and PostgreSQL idempotency
  -> AI-Trader worker publication endpoint -> SQLite signal feed
```

## Market intelligence

Market reads are snapshot-only. The refresh loop calls Alpha Vantage on a bounded
interval and writes category plus compact overview snapshots. Agent turns read SQLite
only, so provider latency or quota does not enter the chat path. Without a snapshot,
read endpoints return a stable `available: false` response with freshness metadata.

Ticker-filtered news reports matched and unmatched symbols so agents cannot infer
coverage from a small unfiltered sample. Current categories are `equities`, `macro`,
`crypto`, and `commodities`; all are research context rather than trading advice.

## Agent tool surface

The `ai-trader-agent` owns:

- `get_ai_trader_market_overview`
- `get_ai_trader_market_news`
- `get_ai_trader_signal_feed`
- `publish_ai_trader_strategy`
- `publish_ai_trader_discussion`
- `poll_ai_trader_heartbeat`

Strategy and discussion publication trigger a native LangGraph interrupt by default.
Only Full-access mode with the separate sensitive-effects opt-in may auto-approve
them for the current actor/thread lease. Trusted application code still owns actor
scoping, evidence-reference validation, deterministic idempotency, transition state,
and postconditions. Model-facing publication schemas do not accept caller-selected
idempotency keys. Heartbeat uses the read permission and remains an explicit protected
call; it is never a hidden background agent effect.

## Storage and authentication

The worker database defaults to `data/ai-trader/clawtrader.db` and creates only
Nexus-owned `nexus_signals` and `nexus_market_snapshots` tables. It ignores and does
not migrate upstream tables. PostgreSQL remains authoritative for governed publication
workflow state; SQLite is the adapter's local signal/snapshot store.

Read-only market/signal endpoints are unauthenticated inside the private worker
boundary. Refresh, publication, and heartbeat require `AI_TRADER_TOKEN`, accepted as
Bearer or `X-Claw-Token`. Backend and worker environments must use the same token.
Production also relies on private service networking and never publishes the worker
port.

## Runtime

`run.sh` starts the worker at `127.0.0.1:8100` and a sibling refresh loop when its
virtual environment is available. Compose runs separate API and refresh services that
share the AI-Trader data volume. The backend base URL is injected by the launcher;
leave `AI_TRADER_API_BASE_URL` empty for normal launcher/Compose use.

The worker exposes `GET /health` and `GET /ready` plus `/api/market-intel/*`,
`/api/signals/*`, and `/api/claw/agents/heartbeat`. Exact local commands, request
examples, and environment names remain with the component in
[`ai-trader-worker/README.md`](../../ai-trader-worker/README.md) and
`ai-trader-worker/.env.example`.

`ALPHA_VANTAGE_API_KEY` is needed only for automatic/manual refresh. Without it, the
worker can still serve the latest snapshot or the typed empty fallback.
