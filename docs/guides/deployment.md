# Deployment Guide

How the demo deployment is built, shipped, and rolled back. The
[Development guide](development.md) covers the private-beta profile and the
local stack; this page covers only the automated path to a single host.

## What is deployed

The demo runs `docker-compose.yml` plus two overlays:

| File | Role |
| --- | --- |
| `docker-compose.demo.yml` | Development-stage backend, loopback-bound frontend, optional workers behind profiles, demo-sized resource ceilings |
| `docker-compose.deploy.yml` | Replaces every `build:` with a published `ghcr.io` image pinned to a commit tag |

The backend runs the `development` build stage, which serves the product with
`langgraph dev`. That is the open-source development runtime: it needs neither a
LangGraph Cloud license key nor a LangSmith API key. It is not a production
posture.

## Security posture

`development` mode is trusted-local only. `backend/source/security/auth.py`
grants every request the development admin identity whenever `NEXUS_ENV` is not
`production` and no OIDC issuer is configured, and
`backend/source/http_app.py` makes `/auth/login` redirect straight to `/chat`
while browser session auth is disabled. `SECURITY.md` states the same rule.

The demo deployment compensates in two places, and both must stay in place:

1. The frontend container publishes to `127.0.0.1:8080` only.
2. The host ingress (`deploy/nginx/nguoimoihoccode.io.vn.conf`) requires HTTP
   basic auth before proxying to it.

The deploy workflow fails if the public endpoint stops returning `401` without
credentials, so losing the gate is treated as a broken deploy rather than a
silent regression. Removing the gate is only appropriate once the private-beta
OIDC profile is what is actually running.

## Flow

```text
push to main
  |
  +-- Quality checks   (tests, image build, vulnerability policy)
  |
  `-- Deploy demo      (workflow_run, only after Quality checks succeeds)
        |
        +-- publish   build the three demo images, push them to GHCR
        |             tagged sha-<7> and latest
        |
        `-- deploy    ssh to the host
                      stage the environment file
                      pin NEXUS_IMAGE_TAG, pull, up -d
                      verify 401 without credentials and 200 with them
                      roll back to the previous tag if verification fails
```

Only the backend, frontend, and AI-Trader worker images are published. The two
quant workers stay behind the `quant` profile and keep building locally, because
their qlib and OpenBB dependency trees are large and the demo does not start
them.

The default start set is therefore five services: `postgres`, `domain-migrate`,
`backend`, `ai-trader-worker`, and `frontend`, with 960 MB of memory ceiling
between the four that keep running. Idle usage is far below that — the beta
profile measured most of these at tens of megabytes. The market-data refresh
worker sits behind the `refresh` profile, off by default, so the demo shows the
market data the worker last held rather than fresh data. Enabling it costs
little on its own, around 33 MB idle, but the demo is a guest on this host: an
unrelated project holds most of the memory and is not containerised, so its
share cannot be capped and the demo yields to it.

Every ceiling keeps its `${VAR:-default}` form, so one service can be raised
from `DEMO_ENV` without editing a file. Check the host before the first deploy —
`free -h` — and raise them if it has more room than the demo was sized for.
Watch `dmesg` or `docker inspect --format '{{.State.OOMKilled}}'` for a kill:
that is the ceiling being too low, not the service failing.

## First-time setup

`deploy/setup-vps.sh` runs once on the host as root. It creates a `deploy`
account in the `docker` group, prepares the checkout, installs the nginx ingress
for `nguoimoihoccode.io.vn`, obtains a certificate, and sets the basic-auth
credential. It is additive: it does not modify the other vhosts on that nginx.

```bash
scp deploy/setup-vps.sh root@<host>:/tmp/
ssh -t root@<host> 'bash /tmp/setup-vps.sh'
```

The `-t` matters. Piping the script in with
`ssh root@<host> 'bash -s' < deploy/setup-vps.sh` puts the script itself on
stdin, where the prompts would consume the remaining lines instead of reading an
answer, and `htpasswd` could not ask for a password at all.

Then create a GitHub Environment named `demo` and fill it in, plus one
repository-scoped variable that cannot live there. The script prints the same
list when it finishes.

| Kind | Scope | Name | Value |
| --- | --- | --- | --- |
| Variable | Repository | `DEPLOY_ENABLED` | `true` |
| Secret | Environment `demo` | `VPS_SSH_KEY` | Private half of a dedicated deploy key |
| Secret | Environment `demo` | `DEMO_ENV` | A filled-in copy of `deploy/.env.example` |
| Secret | Environment `demo` | `DEMO_BASIC_AUTH_USER` | The username chosen during setup |
| Secret | Environment `demo` | `DEMO_BASIC_AUTH_PASSWORD` | The matching password |
| Variable | Environment `demo` | `VPS_HOST` | Host address |
| Variable | Environment `demo` | `VPS_USER` | `deploy` |
| Variable | Environment `demo` | `VPS_KNOWN_HOSTS` | `ssh-keyscan -H <host>` output |

`DEPLOY_ENABLED` is the one value that is not in the Environment, and that is
deliberate rather than an oversight. It is the only thing the deploy job's own
gate reads, and GitHub evaluates a job-level `if` *before* it assigns
`environment:`, so the `vars` context there holds repository variables only.
The gate was originally written against `VPS_HOST`; because that value lives in
the Environment, the condition was permanently false and the job reported
"skipped" on every run — the deploy never happened and nothing said why.
Moving this flag into the Environment would disable the deploy, not secure it.

Everything scoped to the Environment is read while the job runs, after the
Environment has been assigned, so `env:` can use environment variables even
though `if:` cannot.

The deploy key is dedicated to this purpose and separate from any interactive
account. Prefer `VPS_KNOWN_HOSTS` over letting the workflow scan on first
connection.

GHCR packages are created private. Set each of the three packages to public
under Package settings, or the host cannot pull them anonymously.

## Operating a deployment

Rollback is one value. The tag lives in `/opt/nexus/nexus-agent/.env` on the
host, so redeploying a previous build is:

```bash
ssh deploy@<host> 'bash -s -- sha-<previous>' < deploy/remote-deploy.sh
```

`remote-deploy.sh` is idempotent and also usable by hand. Note that it forces
the checkout to `origin/main`; the host is a deploy target, not a place to edit.

Container state is on named volumes, so `down` without `-v` preserves
PostgreSQL and the AI-Trader database. Images older than a week are pruned on
each deploy, which leaves a rollback window of seven days.

## Why not the beta profile here

The beta overlay requires a licensed LangGraph Cloud key. Its backend image is
`langchain/langgraph-api`, which performs a deployment license check at startup
and refuses to serve without either that key or a LangSmith API key. The beta
profile also requires a confidential OIDC client, a tenant HMAC key, and a
browser-session key. That is the supported production posture described in the
[Development guide](development.md); the demo path deliberately avoids it.