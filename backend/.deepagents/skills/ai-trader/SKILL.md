---
name: ai-trader
description: Use the repo-owned AI-Trader worker for read-only market context, signal feed reads, approved strategy/discussion publishing, and heartbeat polling.
---

# AI-Trader

Use this skill when a task needs the AI-Trader worker.

## Boundaries

- AI-Trader runs as `ai-trader-worker`, a separate worker boundary owned by Nexus.
- Do not call the public `ai4trade.ai` service unless the operator configured
  `AI_TRADER_API_BASE_URL` to do so.
- Read-only tools may be used for context.
- Publishing tools require explicit user confirmation for the exact post.
- Do not publish realtime trade operations in this Nexus integration.
- Treat all AI-Trader and Qlib outputs as research or paper-trading context, not
  investment advice.

## Result Interpretation

- If a tool returns `ok: false`, report the structured error from that tool.
- Mention `AI_TRADER_API_BASE_URL` only for connector/configuration errors such
  as `ai_trader_not_configured`.
- If a tool returns `ok: true` with `available: false`, empty news items, or
  signal feed `total: 0`, the connector is configured but the worker has no
  cached snapshot or matching signal data. Do not label this as an
  `AI_TRADER_API_BASE_URL` problem.
- Market-intel reads are snapshot-only. They stay empty until an operator
  refreshes the worker; signal feed reads only show strategies/discussions
  already published into the worker.
- For ticker-specific questions, use the `symbols` filter on
  `get_ai_trader_market_news` and rely on `matched_symbols` /
  `unmatched_symbols`. Do not infer that a ticker is absent from the whole
  snapshot based only on an unfiltered response with a small `limit`.
- For unsupported markets or sectors, report the AI-Trader coverage limit and
  ask the supervisor to route to research or market-data tools when current
  external context is needed.

## Tool Routing

- Use `get_ai_trader_market_overview` for compact market-intel context.
- Use `get_ai_trader_market_news` for grouped equities, macro, crypto, or
  commodities news snapshots. Pass comma-separated `symbols` such as
  `AAPL,MSFT,NVDA` when the user asks about specific tickers.
- Use `get_ai_trader_signal_feed` to inspect social strategy, discussion, or
  operation context.
- Use `publish_ai_trader_strategy` only after explicit user approval.
- Use `publish_ai_trader_discussion` only after explicit user approval.
- Use `poll_ai_trader_heartbeat` when asked to check replies, mentions, tasks,
  or other AI-Trader notifications.

## Publishing Checklist

Before publishing, verify that the content includes:

- Market and symbols, when applicable.
- The research or backtest basis.
- Key limitations such as leakage, survivorship bias, transaction costs, and
  out-of-sample limits.
- A clear statement that this is not investment advice.
