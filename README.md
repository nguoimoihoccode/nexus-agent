# Nexus Agent

Nexus Agent is a product-validation build for source-backed research, OpenBB/Qlib
workflows, and AI-Trader evidence. A LangGraph/Deep Agents supervisor coordinates
four scoped agents; React renders chat, approvals, live execution, and evidence.

Each chat thread can run in Safe, Autonomous-session, or Full-access mode. The two
autonomous modes use short-lived actor/thread authorization leases to reduce repeated
HITL prompts without granting shell access or bypassing role, tenant, validation, or
audit boundaries. Memory deletion and external publication remain manual by default.

## Supported profiles

- Local development.
- Single-node private beta with OIDC, PostgreSQL row-level actor isolation,
  per-service worker authentication, governed approvals, strict checkpoint
  serialization, and PostgreSQL durability.

Multi-replica coordination, external object storage, signed releases, disaster
recovery, capacity certification, and formal SLO operations are intentionally
deferred until product workflow validation justifies them.

## Runtime

```text
Browser -> React/Nginx -> LangGraph supervisor -> PostgreSQL
                           |-> researcher -> web search
                           |-> quant-data-agent -> OpenBB/Qlib data worker
                           |-> quant-researcher -> Qlib worker
                           `-> ai-trader-agent -> AI-Trader worker
```

PostgreSQL owns checkpoints, workflow/evidence state, approvals, authorization
leases, events, and actor-scoped keyed memory with a bounded text/Markdown aggregate.
Quant artifacts use local files for host development and Docker named volumes in the
Compose profiles.

## Quick start

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
cp ai-trader-worker/.env.example ai-trader-worker/.env
docker compose --env-file backend/.env up --build -d
```

Set `DEEPSEEK_API_KEY` in `backend/.env`. Open `http://localhost:8080`; both the
frontend and LangGraph API bind to loopback by default.

For a host-run development stack:

```bash
cd backend && uv sync --extra dev
cd ../frontend && npm install
cd .. && source backend/.venv/bin/activate && ./run.sh
```

Real Qlib execution requires `uv sync --project quant-worker --extra qlib`.
Real OpenBB ingestion requires `uv sync --project quant-data-worker --extra openbb`.

## Verification

```bash
uv run --project backend python scripts/check-architecture.py
uv run --project backend python -m unittest discover -s backend/tests
uv run --project quant-worker python -m unittest discover -s quant-worker/tests
uv run --project quant-data-worker python -m unittest discover -s quant-data-worker/tests
uv run --project ai-trader-worker python -m unittest discover -s ai-trader-worker/tests
npm --prefix frontend test
npm --prefix frontend run build
DEEPSEEK_API_KEY=offline-test uv run --project backend python scripts/run-product-validation.py
```

See [documentation](docs/README.md). Outputs are historical research evidence,
not investment advice.
