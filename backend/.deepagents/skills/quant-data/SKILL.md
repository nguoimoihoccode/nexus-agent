---
name: quant-data
description: Validate and run the MVP OpenBB-to-Qlib data ingestion pipeline, or report structured worker errors.
---

# Quant Data Skill

Use this skill when a task asks to fetch OHLCV data, validate market data, build a
Qlib dataset, validate a Qlib dataset before experiment execution, or fetch
fundamental/factor context for symbols.

## MVP Workflow

1. Confirm the request uses daily OHLCV data, an explicit provider, an explicit
   adjustment policy, a non-empty symbol list, and an ISO date range.
2. Propose the exact `prepare_qlib_dataset` arguments and wait for explicit runtime
   approval. If rejected, stop without retrying or changing the arguments.
3. Call `prepare_qlib_dataset` once with a human-friendly `dataset_alias`; trusted
   workflow code owns fetch, staging validation, build, manifest validation,
   durable transitions, and the canonical revision ID.
4. Require a completed response with `manifest_verified: true`. Do not call the
   lower-level fetch or build boundaries directly; they are not model-visible.
5. If the user asked for fundamentals, valuation, quality, leverage, growth,
   margins, or comparison context, propose `fetch_factor_snapshot` separately and
   wait for its approval before calling it for the same symbols.
6. Return the resulting `dataset_revision_id` only when the final validation succeeds.
7. Final output must satisfy the runtime-enforced `QuantDataHandoff` contract
   from the agent instructions.

## Boundaries

- Use only the provided quant data tools.
- Do not call raw OpenBB, raw Qlib, shell commands, notebooks, files, or web search.
- Do not run Qlib experiments or interpret backtest metrics.
- Do not give investment advice or trade recommendations.
- Do not describe factor snapshots as point-in-time historical data or use them
  as model training features in the MVP.
- Do not modify files, memory, prompts, skills, docs, source code, or artifacts.

## Important

- Report stable IDs and warnings exactly as returned by tools.
- Stop on the first `ok: false` response.
