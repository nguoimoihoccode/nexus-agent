FROM ghcr.io/astral-sh/uv:0.10.6@sha256:2f2ccd27bbf953ec7a9e3153a4563705e41c852a5e1912b438fc44d88d6cb52c AS uv

FROM langchain/langgraph-api:3.11@sha256:3997cebb2212b759272d1774310d215a9b937e70f32f92288d09cb14e6a14e04 AS production-deps

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

USER root

ENV UV_COMPILE_BYTECODE=1 \
    UV_HTTP_TIMEOUT=300 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /deps/backend

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN --mount=type=bind,from=uv_cache,target=/root/.cache/uv,rw \
    --mount=type=secret,id=host_ca,target=/tmp/host-ca-certificates.crt \
    SSL_CERT_FILE=/tmp/host-ca-certificates.crt \
    uv sync --locked --no-dev --no-install-project

COPY . .
RUN --mount=type=bind,from=uv_cache,target=/root/.cache/uv,rw \
    --mount=type=secret,id=host_ca,target=/tmp/host-ca-certificates.crt \
    SSL_CERT_FILE=/tmp/host-ca-certificates.crt \
    uv sync --locked --no-dev --no-editable

FROM langchain/langgraph-api:3.11@sha256:3997cebb2212b759272d1774310d215a9b937e70f32f92288d09cb14e6a14e04 AS production

ARG APP_UID=10001
ARG APP_GID=10001
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

USER root

COPY --from=production-deps --chown=10001:10001 /deps/backend /deps/backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade \
        libcurl3t64-gnutls \
    && apt-get purge -y git \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/deps/backend/.venv/bin:$PATH" \
    PYTHONPATH="/deps/backend/.venv/lib/python3.11/site-packages:/deps/backend" \
    LANGSERVE_GRAPHS='{"supervisor": "/deps/backend/source/agents/agent.py:graph"}' \
    LANGGRAPH_AUTH='{"path": "/deps/backend/source/security/auth.py:auth"}' \
    LANGGRAPH_HTTP='{"app": "/deps/backend/source/http_app.py:app", "enable_custom_route_auth": true, "middleware_order": "auth_first", "disable_mcp": true, "disable_a2a": true, "disable_webhooks": true}' \
    LANGGRAPH_STRICT_MSGPACK='true' \
    LANGGRAPH_CHECKPOINTER='{"ttl": {"strategy": "delete", "sweep_interval_minutes": 60, "default_ttl": 43200}, "serde": {"allowed_json_modules": [], "pickle_fallback": false}}'

RUN test -f /api/langgraph_api/__init__.py \
    && test -f /api/langgraph_runtime/__init__.py \
    && test -f /api/langgraph_license/__init__.py \
    && rm -rf \
        /usr/local/lib/python*/site-packages/pip* \
        /usr/local/lib/python*/site-packages/setuptools* \
        /usr/local/lib/python*/site-packages/wheel* \
        /usr/lib/python*/site-packages/pip* \
        /usr/lib/python*/site-packages/setuptools* \
        /usr/lib/python*/site-packages/wheel* \
        /usr/local/lib/python*/ensurepip \
    && find /usr/local/bin /usr/bin -name "pip*" -delete \
    && rm -f /usr/bin/uv /usr/bin/uvx /usr/local/bin/uv /usr/local/bin/uvx

RUN if command -v groupadd >/dev/null 2>&1; then \
        groupadd --system --gid "$APP_GID" nexus; \
        useradd --system --uid "$APP_UID" --gid "$APP_GID" --home-dir /nonexistent --shell /usr/sbin/nologin nexus; \
    else \
        addgroup -S -g "$APP_GID" nexus; \
        adduser -S -D -H -u "$APP_UID" -G nexus nexus; \
    fi \
    && chown -R "$APP_UID:$APP_GID" /deps/backend /tmp

WORKDIR /deps/backend

EXPOSE 8000

USER 10001:10001

FROM python:3.11-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba AS development

ARG APP_UID=10001
ARG APP_GID=10001

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=bind,from=uv_cache,target=/root/.cache/uv,rw \
    --mount=type=secret,id=host_ca,target=/tmp/host-ca-certificates.crt \
    SSL_CERT_FILE=/tmp/host-ca-certificates.crt \
    uv sync --frozen --no-install-project --extra dev

COPY . .
RUN --mount=type=bind,from=uv_cache,target=/root/.cache/uv,rw \
    --mount=type=secret,id=host_ca,target=/tmp/host-ca-certificates.crt \
    SSL_CERT_FILE=/tmp/host-ca-certificates.crt \
    uv sync --frozen --no-install-project --extra dev

RUN groupadd --system --gid "$APP_GID" nexus \
    && useradd --system --uid "$APP_UID" --gid nexus --home-dir /nonexistent --shell /usr/sbin/nologin nexus \
    && chown -R nexus:nexus /app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 2024

USER nexus

CMD ["langgraph", "dev", "--no-browser", "--no-reload", "--host", "0.0.0.0", "--port", "2024"]
