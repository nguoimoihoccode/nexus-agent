# Development Guide

## Requirements

- Python 3.11 and `uv`
- Node.js 22 and npm
- PostgreSQL 16 (the supported and pinned beta major)
- Docker Compose for the container profile

Never commit `.env` files, credentials, generated data, or local logs.
`backend/.env.example` intentionally lists only credentials and deployment identity
without safe application defaults. Runtime URLs, budgets, logging and resource limits
remain owned by typed settings or Compose defaults and can still be overridden from
the process environment when needed.

## Docker quick start

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
cp ai-trader-worker/.env.example ai-trader-worker/.env
docker compose --env-file backend/.env up --build -d
```

Set the required model credential in `backend/.env`. The frontend is available at
`http://localhost:8080`; the LangGraph development API is loopback-bound on port
`2024`. Setting `APP_BIND_HOST=0.0.0.0` is an explicit trusted-LAN opt-in and must
never be used with the development admin on an untrusted network.

## Host-run stack

```bash
cd backend && uv sync --extra dev
cd ../quant-worker && uv sync
cd ../quant-data-worker && uv sync
cd ../ai-trader-worker && uv sync --extra dev
cd ../frontend && npm install
cd .. && source backend/.venv/bin/activate && ./run.sh
```

`run.sh` checks PostgreSQL and expected ports, applies local domain migrations unless
disabled, then starts every worker with an available environment plus backend and
frontend. It preserves inherited HTTP(S) proxy settings while forcing loopback traffic
through `NO_PROXY`. Backend reload is disabled by default so LangGraph's periodic
`.langgraph_api` persistence writes do not trigger the source watcher; run
`NEXUS_BACKEND_RELOAD=1 ./run.sh` when backend hot reload is explicitly needed. Real
Qlib execution needs `uv sync --project quant-worker --extra qlib`; real OpenBB
ingestion needs `uv sync --project quant-data-worker --extra openbb`.

Vite binds to loopback by default and proxies both `/api` and `/auth` to the backend.
Only use the explicit trusted-LAN profile on a network you control, with an explicit
backend target:

```bash
cd frontend
NEXUS_TRUSTED_LAN=1 \
VITE_API_PROXY_TARGET=http://192.168.1.10:2024 \
  npm run dev:lan
```

The LAN script supplies the non-loopback bind and trusted-network opt-in; Vite fails
closed unless an explicit proxy target is also configured. The local backend keeps
`NEXUS_BROWSER_SESSION_AUTH=disabled`; the browser still uses the same `/auth/session`
contract but receives the built-in development identity. Never expose that local admin
identity on an untrusted interface.

| Service | Host-run URL |
| --- | --- |
| Frontend | `http://localhost:5173` |
| LangGraph/backend | `http://127.0.0.1:2024` |
| Quant worker | `http://127.0.0.1:8000` |
| Quant data worker | `http://127.0.0.1:8001` |
| AI-Trader worker | `http://127.0.0.1:8100` |

Logs and generated data stay under `data/`. A missing optional worker yields a
structured tool error; it must not be mistaken for a successful end-to-end workflow.

The default `backend/langgraph.json` exposes only `supervisor`. To debug standalone
subagents explicitly in local Studio, use the loopback-only profile:

```bash
cd backend
uv run langgraph dev --config langgraph.studio.json --host 127.0.0.1
```

Standalone runs require `studio:access`; the no-OIDC local actor has the development
admin role. Do not expose this profile on a public interface.

## Database migrations

```bash
uv run --project backend python -m source.infrastructure.migrations status
uv run --project backend python -m source.infrastructure.migrations upgrade
```

Compose runs `domain-migrate` before application services. Runtime startup verifies
the schema version/checksum but does not mutate it. `bootstrap` also provisions the
current least-privilege database roles. Use rollback only on disposable local data
with the explicit destructive flag; prefer forward migrations once data exists.

Migration 0008 remains immutable and creates the original aggregate memory table.
Migrations 0009 and 0010 add actor policies and enable row-level security. Migration
0011 adds the keyed-memory table as a forward-only repair. Upgrade accepts the one
known historical 0008 checksum that briefly included the keyed table, then applies
0011 idempotently; all other checksum drift still fails closed. Migration 0012 adds
actor/thread-scoped authorization leases with row-level security, a four-hour maximum
TTL, revocation history, and no authority to expand the actor's OIDC role permissions.
Migration 0013 adds the actor-scoped keyset index used by the experiment catalog; it
does not rewrite existing experiment data. Migration 0014 adds short-lived, one-time
OIDC login transactions and opaque browser sessions. Only SHA-256 hashes of state,
temporary browser binding, and session cookie values are stored; PKCE verifiers and
OIDC refresh/ID credentials are encrypted with the separate browser-session key.

PostgreSQL integration suites require a disposable administrator database:

```bash
NEXUS_TEST_POSTGRES_URI=postgresql://... \
NEXUS_TEST_POSTGRES_ADMIN_URI=postgresql://... \
  uv run --project backend python -m unittest \
  backend.tests.test_domain_postgres_integration
```

The quality workflow runs the corresponding quant-data and quant-worker integration
suites against the same disposable PostgreSQL service.

## Private-beta profile

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.beta.yml \
  --env-file /path/to/beta.env \
  up --build -d
```

The overlay requires non-default PostgreSQL credentials, separate application
database logins, separate quant and quant-data worker credentials, confidential HTTPS
OIDC settings, a tenant HMAC key, a separate 32-byte browser-session key, and a
16/24/32-byte checkpoint encryption key. It uses
named volumes, read-only root filesystems, and a default 16 GB-node resource profile.
Every CPU, memory, and PID limit can be overridden by its corresponding `*_LIMIT`
environment setting. See Compose interpolation errors and `.env.example` files for
exact names; never duplicate real values in documentation.

Register the backend as a confidential OIDC client. Its exact redirect URI is
`<NEXUS_APP_ORIGIN>/auth/callback` and its post-logout redirect is
`<NEXUS_APP_ORIGIN>`; `NEXUS_APP_ORIGIN` must be one HTTPS origin without a trailing
slash. Set `NEXUS_OIDC_CLIENT_ID`, a client secret of at least 32 characters,
`NEXUS_OIDC_SCOPE` (normally `openid profile`), and an independent random
`NEXUS_BROWSER_SESSION_KEY` of exactly 32 bytes. For example, generate the latter with
`openssl rand -hex 16` and store it in the deployment secret manager. Do not reuse the
tenant HMAC key, checkpoint key, or OIDC client secret.

Production uses Authorization Code + PKCE with `response_mode=form_post`; callback
credentials therefore stay out of request URLs and access logs. Access logs also omit
query strings, headers, and cookies. Access, refresh, and ID tokens remain on the
server. A ten-minute Secure/HttpOnly/SameSite=None `__Host-nexus_login` cookie binds
the cross-site form callback to the browser that initiated login and is cleared after
the exchange. The browser then receives only a Secure, HttpOnly, SameSite=Lax
`__Host-nexus_session` cookie plus a per-session CSRF value. Sessions idle out after
one hour and have an eight-hour absolute maximum by default. Changing these TTLs
requires values within the bounds enforced by typed settings.

TLS terminates at the trusted ingress in front of the loopback-published frontend.
That ingress must add HSTS only after HTTPS is working for the complete hostname; the
internal HTTP Nginx container must not claim HSTS on behalf of a connection it cannot
verify. The frontend image enforces CSP, clickjacking, MIME-sniffing, referrer,
Permissions Policy, COOP/CORP, and cache headers. Trusted Types remains report-only;
review `/auth/csp-report` telemetry before promoting it to enforcement.

Worker data is stored in Docker named volumes so runtime UID ownership does not
depend on host directories. Beta healthchecks call each authenticated `/ready`
endpoint and therefore include storage/database dependency state. The quant-data
image prebuilds the OpenBB extension registry; runtime cache/home paths remain under
writable `/tmp`, including with a read-only root filesystem.

## Local quality gate

Run focused tests first, then the relevant full gate:

```bash
uv run --project backend python scripts/check-architecture.py
uv run --project backend python -m unittest discover -s backend/tests
uv run --project backend python -m compileall backend/source

uv run --project quant-worker python -m unittest discover -s quant-worker/tests
uv run --project quant-data-worker python -m unittest discover -s quant-data-worker/tests
uv run --project ai-trader-worker python -m unittest discover -s ai-trader-worker/tests

npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run test:e2e

DEEPSEEK_API_KEY=offline-test \
  uv run --project backend python scripts/run-product-validation.py \
  --output data/evaluation/product-validation-v1.json
./scripts/security-audit.sh
```

CI also runs direct PostgreSQL integration, application image build/smoke, production
frontend header assertions, and a high/critical container vulnerability policy. The
scheduled security workflow additionally runs a pinned OWASP ZAP baseline against the
production Nginx boundary; HSTS is its only ignored alert because TLS terminates at the
trusted ingress. The deterministic acceptance suite is described in
[Product validation](product-validation.md).

PostgreSQL major upgrades are deliberate compatibility work: Dependabot ignores
automatic major updates for the Compose image while still proposing supported patch
and minor image updates.
