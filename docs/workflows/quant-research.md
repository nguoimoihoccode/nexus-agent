# Quant Research Workflow

Nexus implements one narrow, research-only OpenBB-to-Qlib path:

```text
User -> supervisor
  -> optional researcher context
  -> quant-data-agent
      -> exact-argument approval
      -> OpenBB OHLCV/factor fetch
      -> staging validation
      -> immutable Qlib provider build and validation
  -> quant-researcher
      -> dataset validation
      -> exact-argument approval
      -> allowlisted Qlib experiment
      -> result retrieval
      -> separate approval for optional evidence-linked interpretation
  -> supervisor response
```

Agents call high-level JSON tools. They do not run shell commands, notebooks, raw
OpenBB APIs, or raw Qlib APIs.

## Supported scope

| Area | Current support |
| --- | --- |
| Provider | OpenBB with `yfinance` |
| Market data | Daily OHLCV and Qlib-compatible `factor` |
| Factor context | Current provider snapshot only; never historical model features |
| Dataset | Immutable manifest-verified Qlib provider layout under `data/qlib` |
| Feature set | `Alpha158` |
| Models | `lightgbm`, `linear`, `xgboost`, `catboost` |
| Strategy | `topk_dropout` |
| Execution | Durable jobs; duplicate active requests reuse the experiment identity |

The Qlib worker never downloads data at startup. Build datasets through
`quant-data-worker`. Legacy manually placed dataset directories can be read for local
research, but they have no verified manifest or production eligibility; rebuild them
through the current workflow when lineage matters.

## Identity and lineage

| ID | Meaning |
| --- | --- |
| `request_fingerprint` | Versioned hash of normalized provider parameters, excluding retrieval time |
| `content_hash` | SHA-256 identity of canonical normalized provider content |
| `staging_revision_id` | Immutable OHLCV revision; `dataset_staging_id` is a transitional alias |
| `factor_snapshot_revision_id` | Immutable current factor revision; `factor_snapshot_id` is a transitional alias |
| `dataset_alias` | Human label for build intent; never canonical storage identity |
| `dataset_revision_id` | Immutable Qlib revision derived from the canonical file manifest |
| `experiment_id` | Durable Qlib job/result identity |

Agents pass returned IDs forward without reconstructing them. Identical parameters
and normalized content reuse a revision; changed content creates a new revision while
preserving earlier data. Actor-scoped links retain request-to-revision history.
Cross-service golden vectors live in
[`reference/canonical-identity-v1.json`](../reference/canonical-identity-v1.json).

## Model-visible tools

| Agent | Tool | Responsibility |
| --- | --- | --- |
| `quant-data-agent` | `prepare_qlib_dataset` | Fetch, validate, build, and validate one dataset as a durable composite workflow |
| `quant-data-agent` | `fetch_factor_snapshot` | Fetch current factor context, explicitly not point-in-time history |
| `quant-researcher` | `list_quant_datasets` | List actor-visible datasets and coverage |
| `quant-researcher` | `run_governed_qlib_experiment` | Preflight and submit an allowlisted experiment workflow |
| `quant-researcher` | `get_qlib_experiment_result` | Poll/read bounded normalized status and result |
| `quant-researcher` | `record_experiment_interpretation` | Store bounded research interpretation linked to exact evidence IDs |

Lower-level worker fetch/build/validate, cancellation, and artifact endpoints are
application-controlled, not separate model-visible effects. Dataset preparation
accepts 1-300 unique symbols, daily frequency, `yfinance`, and an explicit adjustment
policy. Canonical staged data columns are:

```text
date, symbol, open, high, low, close, volume, factor
```

Experiments require ordered non-overlapping train/validation/test ranges. Preflight
checks dataset coverage and the Alpha158 label-lookahead boundary before consuming a
worker slot. Completed results return JSON-safe metrics and relative artifact IDs;
failures return stable safe codes, stages, and retryability rather than traceback or
raw framework objects.

The default quant-researcher budget allows 12 tool calls and 16 model calls per
delegation so dataset inspection, approval, submission, bounded result polling and a
structured handoff have operational headroom. These are per-run execution guards,
not provider rate limits and not time-based quotas. Experiment submission and
interpretation persistence remain independently capped at one call each.

All four model-visible quant effects require a durable approval record. Safe mode
uses a native approval interrupt; an active Autonomous-session or Full-access lease
may auto-approve them for its exact actor and thread. Execution remains bound to the
actor, exact normalized arguments, target boundary, and tool-call ID; changing any of
them invalidates the approval. Model-facing schemas expose neither `force_new` nor
caller-selected idempotency keys. Dataset/result reads remain read-only and do not
interrupt.

## Agent boundaries

| Agent | Owns | Excludes |
| --- | --- | --- |
| `researcher` | Source-backed market, academic, regulatory, or provider context | Dataset creation, Qlib runs, investment advice |
| `quant-data-agent` | Fetch, validation, dataset build, current factor context | Training, strategy choice, result interpretation |
| `quant-researcher` | Dataset inspection, experiments, limitations, interpretation | Fetch/build, unsupported models/strategies, investment advice |

Quant subagents return validated `QuantDataHandoff` or `QuantExperimentHandoff`
schema version `1.0`. The supervisor may use typed tool-returned references, metrics,
warnings, and errors. Handoff prose and `next_action` are untrusted advisory data;
cross-agent routing values are sanitized before reaching the supervisor. Malformed
handoff repair cannot issue another domain tool call.

## Research limitations

Datasets and backtests are evidence, not investment advice. `yfinance` output is
research-only: point-in-time membership and fundamental availability are unsupported,
and provider revisions/corporate-action semantics are not fully verifiable. Reviews
must consider survivorship bias, lookahead leakage, adjustment policy, missing trading
days, symbol mapping, transaction costs, and out-of-sample limits.
