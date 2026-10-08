FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

FROM python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89 AS builder

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

ENV UV_COMPILE_BYTECODE=1 \
    UV_HTTP_TIMEOUT=300 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=host_ca,target=/tmp/host-ca-certificates.crt \
    SSL_CERT_FILE=/tmp/host-ca-certificates.crt \
    uv sync --locked --no-dev --no-install-project --extra openbb

COPY worker ./worker
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=host_ca,target=/tmp/host-ca-certificates.crt \
    SSL_CERT_FILE=/tmp/host-ca-certificates.crt \
    uv sync --locked --no-dev --no-editable --extra openbb
RUN HOME=/tmp XDG_CACHE_HOME=/tmp/.cache \
    .venv/bin/python -c 'from openbb import obb; assert obb is not None'

FROM python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QUANT_DATA_WORKER_DATA_DIR=/data/qlib \
    QUANT_DATA_WORKER_STAGING_DIR=/data/staging \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

RUN rm -rf \
        /usr/local/lib/python3.11/ensurepip \
        /usr/local/lib/python3.11/site-packages/pip* \
        /usr/local/lib/python3.11/site-packages/setuptools* \
        /usr/local/lib/python3.11/site-packages/wheel* \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11 \
    && groupadd --system --gid 10001 nexus \
    && useradd --system --uid 10001 --gid nexus --home-dir /nonexistent --shell /usr/sbin/nologin nexus \
    && mkdir -p /data/qlib /data/staging \
    && chown -R nexus:nexus /app /data

EXPOSE 8000

USER nexus

CMD ["uvicorn", "worker.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
