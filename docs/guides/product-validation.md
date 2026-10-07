# Product Validation

## Goal and boundary

The current goal is to develop the product and prove that its supported workflows
are stable, actor-isolated, recoverable, appropriately approved, and secure enough
for local use plus one private-beta node. This is not a claim of general production
readiness.

The implemented baseline includes:

- versioned supervisor/subagent handoffs with bounded failure behavior;
- composite OpenBB-to-Qlib preparation and durable experiment workflows;
- PostgreSQL job/control state, idempotency, events, approvals, and evidence lineage;
- OIDC and actor isolation, worker authentication, bounded input, strict checkpoint
  serialization, governed approvals, and short-lived actor/thread authorization leases;
- explicit-consent keyed PostgreSQL user memory with revisioning, Settings reads,
  and approval-gated deletion through the supervisor;
- AI-Trader cached context and governed publications;
- actor-scoped local artifacts protected by immutable manifests and content hashes.

## Deterministic acceptance suite

The versioned manifest is `evaluation/product-validation-v1.json`; the runner is
`scripts/run-product-validation.py`.

```bash
DEEPSEEK_API_KEY=offline-test \
  uv run --project backend python scripts/run-product-validation.py \
  --output data/evaluation/product-validation-v1.json
```

Every manifest scenario tagged `acceptance_failure` blocks the command when it fails.
The report records per-scenario status plus workflow completion and categorized
failure rates. The suite is offline and deterministic: it makes no paid API or model
calls.

Current scenario categories cover:

- workflow ordering, validation, structured contracts, and fail-closed tool surfaces;
- idempotency across duplicate actions, reconnects, cancellation, and worker restart;
- tenant isolation, manifest/artifact integrity, and safe evidence references;
- financial-correctness boundaries such as point-in-time limitations and Alpha158
  lookahead;
- browser replay/resume behavior and unsafe rendered links;
- AI-Trader empty-provider behavior and input safety.
- permission-mode sensitive defaults and the browser's autonomous-mode control.

This suite is one layer of evidence, not the whole quality gate. Unit suites,
PostgreSQL integration, browser E2E, image smoke, and `scripts/security-audit.sh`
remain separate required checks. Exact commands are in the
[Development guide](development.md).

## How to interpret a green result

A green run proves only the versioned scenarios against the current code and pinned
dependencies. It does not prove live provider availability, model answer quality,
capacity, off-host recovery, or all possible financial-data limitations. A live beta
journey should still verify research, dataset creation, experiment execution,
approval/resume, reconnect, and the Experiments catalog/dossier UI with
representative users.

## Deferred until evidence requires it

- multi-replica coordination and distributed admission control;
- remote artifact storage and off-host backup/restore drills;
- registry publishing and release-provenance automation;
- formal capacity/soak certification, SLOs, alerts, and operations dashboards;
- environment-specific outage orchestration.

These are not accepted implicitly. Promote one only when private-beta evidence shows
a concrete reliability, scale, compliance, or recovery need.

## Product loop

1. Run representative end-to-end journeys on the private beta.
2. Capture friction or failures as a minimal reproducible scenario.
3. Fix the smallest responsible boundary and add focused regression evidence.
4. Add a deterministic manifest scenario only when it represents a durable product
   acceptance signal.
5. Reassess deferred infrastructure from measured demand.
