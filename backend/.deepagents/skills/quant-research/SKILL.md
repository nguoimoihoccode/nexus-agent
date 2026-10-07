---
name: quant-research
description: Validate data, run one allowlisted Qlib experiment, and interpret its signal and portfolio metrics.
---

# Quant Research Skill

1. List or inspect the requested Qlib `dataset_revision_id` and `universe`, then read the
   reported calendar coverage. Do not assume a default dataset. If the supervisor
   did not supply a validated, manifest-verified dataset revision and universe,
   return `ask_user` or request data ingestion through the quant-data-agent.
2. Confirm train, validation and test ranges are ordered, non-overlapping and within
   that coverage. Use `max_test_end`, not the raw dataset end, for the Alpha158 test
   boundary. Do not guess dates that the dataset metadata does not support.
3. Propose the exact `run_governed_qlib_experiment` arguments and wait for explicit
   runtime approval. A rejection is terminal; do not change arguments and retry.
4. Call `run_governed_qlib_experiment` once. Trusted workflow code owns manifest
   validation, preflight, idempotency, submission, and durable resume. Do not call
   the lower-level submit boundary directly.
5. Poll the returned experiment ID for a bounded time.
6. For a completed result, propose one bounded interpretation with exact metric and
   artifact IDs, wait for a separate approval, then call
   `record_experiment_interpretation` once.
7. Report configuration, signal metrics, portfolio metrics, artifact references,
   and the returned interpretation report ID.
8. If pending, report the experiment ID without estimating results.
   If failed, report the exact error and do not resubmit or describe it as transient
   unless the tool explicitly marks it retryable.
9. State that historical backtests are not investment advice and flag leakage,
   survivorship, point-in-time and cost assumptions.
10. Final output must satisfy the runtime-enforced `QuantExperimentHandoff`
   contract from the agent instructions.
