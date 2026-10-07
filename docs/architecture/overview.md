# System Architecture

Nexus Agent is a product-validation system for local development and one private-beta
node. It does not claim multi-node coordination, off-host disaster recovery, formal
SLO certification, or a signed release supply chain.

## Runtime flow

```text
Browser
  -> TypeScript + React 19 / TanStack Router + Query / Nginx
  -> same-origin /auth BFF (confidential OIDC code + PKCE, opaque HttpOnly session)
  -> authenticated LangGraph HTTP/SSE and /v1 product APIs through Nginx
  -> supervisor graph
      |-> researcher -> web search
      |-> quant-data-agent -> quant-data-worker -> OpenBB
      |-> quant-researcher -> quant-worker -> Qlib
      `-> ai-trader-agent -> ai-trader-worker -> Alpha Vantage snapshots
  -> PostgreSQL checkpoints, workflow control, approvals, leases, events, evidence, memory
  -> actor-scoped local dataset, artifact, and AI-Trader snapshot storage
```

The frontend loads topology from authenticated `GET /v1/topology`; it has no editable
or simulated copy of the harness. TanStack Router owns reload-safe chat, experiment
catalog/dossier/comparison, capability, and settings URLs. The backend owns OIDC login,
callback, refresh, logout, and session contracts so OAuth credentials never enter
JavaScript or browser storage. TanStack
Query owns topology, health, experiment catalog/evidence, lineage, comparison, and
memory server state. Zod rejects malformed product API payloads, strips unknown fields,
and bounds JSON collections and response sizes at the browser boundary. HTTP/SSE events remain in the custom chat
state machine so abort, reconnect, replay, approval, and stale-run guards stay explicit.
These events drive chat, the execution trace, and graph activity. Product APIs expose
event replay, memory, readiness, lineage, actor-scoped experiment catalog,
dossiers/comparison/export, and verified artifact downloads. Assistant responses
render only strictly shaped experiment IDs as internal dossier links; PostgreSQL,
not streamed prose or browser state, remains the catalog authority.

## Runtime components

| Component | Responsibility | Durable state |
| --- | --- | --- |
| `backend/` | Authentication, graph composition, workflow control, product APIs | PostgreSQL checkpoints and `nexus_domain` records |
| `frontend/` | TypeScript React shell, chat/experiments/capabilities/settings UI, same-origin session client, SSE guards and graph | URL state, query cache, and non-sensitive browser-session UI state only |
| `quant-data-worker/` | OpenBB fetch, validation, immutable staging and Qlib dataset build | Actor-scoped files plus beta PostgreSQL revision/control records |
| `quant-worker/` | Allowlisted Qlib execution, cancellation, results, artifact verification | Beta PostgreSQL job state plus actor-scoped artifact files |
| `ai-trader-worker/` | Cached market intelligence, signal feed, publication endpoint | Nexus-owned SQLite tables on the node |

Workers are internal HTTP boundaries. The backend calls their contracts through
`backend/source/integrations/`; it does not import worker Python packages.

## Agent harness

The supervisor prompt lives in `backend/source/agents/factory.py`. Project subagent
prompts, skills, and UI metadata live under:

```text
backend/.deepagents/agents/<agent>/AGENTS.md
backend/.deepagents/skills/<skill>/SKILL.md
backend/.deepagents/nexus.json
```

`nexus.json` enables agents and assigns product tools/skills. Startup validates the
registry against local prompt and skill files. Executable Python owns permissions,
approval/lease rules, tool budgets, and structured handoff validation. Assigned skills are
injected as trusted system context while graphs are built; models receive no
filesystem reader for progressive skill loading.

| Agent | Product responsibility | Model-visible tools |
| --- | --- | --- |
| `supervisor` | Delegation and explicit-consent user memory | save/delete memory, delegation |
| `researcher` | Current source-backed research | `web_search` |
| `quant-data-agent` | Dataset preparation and factor context | `prepare_qlib_dataset`, `fetch_factor_snapshot` |
| `quant-researcher` | Dataset inspection, Qlib experiments, interpretation | list/run/result/interpretation tools |
| `ai-trader-agent` | Market context and governed publication | read, publish, and heartbeat tools |

Subagents return versioned Pydantic handoffs. One tool-free formatting repair is
allowed; invalid handoffs fail closed and cannot repeat a side effect. Handoff prose
and `next_action` remain untrusted data and cannot authorize another delegation.
Shell and all host-filesystem tools are hidden and execution-blocked; `write_todos`
uses graph state only. Default development and production expose only `supervisor`.
`langgraph.studio.json` explicitly enables standalone debugging, whose runs also
require `studio:access`.

## Persistence and ownership

| Area | Source owner |
| --- | --- |
| Graph lifecycle and composition | `backend/source/bootstrap.py`, `backend/source/agents/` |
| Authentication and actor permissions | `backend/source/security/` |
| Product APIs | `backend/source/http_app.py` |
| Domain schema and repositories | `backend/migrations/`, `backend/source/infrastructure/` |
| Composite dataset/experiment workflows | `backend/source/application/` |
| Worker contracts and clients | `backend/source/contracts/`, `backend/source/integrations/` |
| Worker domain/application/adapters | each worker's `worker/` package |
| Browser routes and application shell | `frontend/src/routes/`, `frontend/src/App.tsx` |
| Browser server state and runtime validation | `frontend/src/api/`, TanStack Query hooks |
| Browser SSE protocol and state guards | `frontend/src/api/chat*.ts`, `frontend/src/features/chat/`, `frontend/src/hooks/useChat.ts` |

PostgreSQL is the authority for checkpoints, workflow/job control, idempotency,
approvals, actor/thread authorization leases, replayable events, evidence lineage,
user memory, one-time OIDC transactions, and opaque browser sessions. The session
tables store only state/browser-binding/session hashes and AES-GCM-encrypted server
credentials; the session encryption key stays outside PostgreSQL. `langgraph` owns
checkpoint tables; `nexus_domain` owns product state. Production provisions separate
backend, quant-data, and quant-worker database roles. Actor-keyed tables also enforce
PostgreSQL row-level security from a connection-scoped actor setting. Cross-actor
event projection and quant scheduling use narrowly scoped, role-checked database
routines that expose only the required projection or work-item metadata. Workers may
use filesystem-only control state in development tests when
`NEXUS_DOMAIN_DATABASE_URL` is absent; production fails closed instead of using that
fallback.

User memory is stored as actor-scoped keyed facts. Saving a fact upserts only its
stable generic key, so a later preference cannot replace an unrelated identity fact.
The repository also materializes a deterministic text/Markdown aggregate, limited to
16 KiB, for the existing API and prompt middleware. Migration 0008 owns the original
aggregate table; forward migration 0011 adds keyed items and safely converges the one
known historical 0008 variant. Save/delete requires explicit user intent and a
durable approval record. Settings can read memory directly, but deletion submits the
`delete_user_memory` action through the supervisor and resumes only after the user
approves or an explicitly sensitive-enabled Full-access lease authorizes that exact
thread action; there is no direct HTTP DELETE bypass. Model middleware injects the
aggregate as untrusted user reference context, never system authority. Control
records retain only safe keys, hashes, byte counts, and revisions. No memory is
stored in repository or runtime files.

Dataset and artifact bytes remain actor-scoped filesystem objects on the single node.
Immutable manifests, hashes, lifecycle metadata, and control state protect those
objects; remote object storage is deferred.

Permission mode is independent from OIDC roles: it cannot grant a permission the
actor does not already have. Safe mode interrupts for every governed effect.
Autonomous-session mode auto-approves only the quant workflow effects. Full-access
mode additionally covers memory saves; memory deletion and AI-Trader publication stay
manual unless the user separately enables sensitive effects. Migration 0012 stores
short-lived leases for one actor and one thread, capped at four hours. Replacement,
expiry, and revocation are server-authoritative. Auto-approved calls still create the
same exact-action approval record and execution capability; audit metadata identifies
the authorization lease as the decision source.

Experiment discovery is an actor-scoped, newest-first keyset catalog over PostgreSQL.
Migration 0013 adds the supporting `(actor_key, created_at, experiment_id)` index.
Catalog summaries expose only allowlisted specification fields; full metrics,
artifacts, lineage and governed-effect evidence remain in the dossier APIs.

## Security boundary

- Production verifies OIDC issuer, audience, asymmetric signature, expiry, roles, and
  actor ownership. A confidential backend-for-frontend performs Authorization Code +
  PKCE, nonce/state validation, a short-lived HttpOnly cookie binding the login to its
  initiating browser, and server-side refresh. The browser has no bearer,
  refresh, or ID token; it uses an opaque Secure/HttpOnly/SameSite cookie, exact-origin
  and Fetch Metadata checks, and HMAC-bound CSRF protection. Local development may use
  the built-in local actor on loopback.
- Internal quant workers require separate service credentials and a pseudonymous
  actor key in production; AI-Trader retains its own credential.
- Durable approvals guard memory writes/deletes, AI-Trader publications, dataset
  preparation, factor snapshots, experiment submission, and interpretation
  persistence. The decision is either a native human interrupt or a verified
  actor/thread lease. Memory and quant effects additionally require a
  function-boundary capability bound to actor, exact arguments, and tool-call identity.
- Quant authorization is split into read, data-write, experiment-run, and
  interpretation-write scopes while preserving the current `analyst` role behavior.
- Inputs are bounded; production checkpoints use strict serialization and encrypted
  content fields. Browser prompts, SSE frames/streams, replay state, approval actions,
  product JSON responses, identifiers, and restored message history have explicit
  size/shape limits. Unknown or malformed governed actions fail closed.
- Production Nginx enforces a same-origin CSP, frame/object/base restrictions,
  MIME-sniffing and referrer controls, Permissions Policy, COOP/CORP and explicit cache
  policy. Trusted Types is report-only until collected violations show enforcement is
  compatible. HSTS belongs to the HTTPS-terminating ingress.
- Beta Compose keeps backend/workers on private networking, publishes the frontend
  only on loopback for trusted ingress, uses named data volumes and read-only root
  filesystems, bounds CPU/memory/PIDs, drops capabilities, uses non-root users, and
  gates startup on authenticated dependency-aware readiness checks.
- CI runs architecture, unit/integration, browser, image-smoke, product-validation,
  full-history secret, static-analysis, dependency, SBOM, and high/critical
  vulnerability gates, including the pinned PostgreSQL image. It asserts headers on
  the production frontend image and runs a pinned weekly OWASP ZAP baseline against
  that Nginx boundary.

## Durable decisions

1. Support local development and one private-beta node; add distributed operations
   only from measured product demand.
2. Keep durable product authority in PostgreSQL. Runtime memory is disposable; user
   memory is explicit actor-scoped PostgreSQL data.
3. Expose composite workflows to models. Trusted application code owns validation,
   identities, transitions, retries, and postconditions.
4. Fail closed on invalid structured handoffs and never repair them by repeating
   domain effects.
5. Keep quant artifact bytes on the single-node filesystem with immutable manifests;
   defer remote storage.
6. Apply security proportional to the private-beta boundary: authentication,
   isolation, approvals, bounded inputs, strict serialization, least privilege, and
   vulnerability gates remain required.
7. Treat executable product-workflow evidence—not hypothetical operational scope—as
   the acceptance baseline.
8. Keep OAuth tokens and refresh capability behind the backend-for-frontend boundary;
   browser code receives only opaque session state and CSRF material.
