FROM ghcr.io/astral-sh/uv:0.10.6@sha256:2f2ccd27bbf953ec7a9e3153a4563705e41c852a5e1912b438fc44d88d6cb52c AS uv

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
    uv sync --locked --no-dev --no-install-project --extra qlib

COPY worker ./worker
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=host_ca,target=/tmp/host-ca-certificates.crt \
    SSL_CERT_FILE=/tmp/host-ca-certificates.crt \
    uv sync --locked --no-dev --no-editable --extra qlib

FROM python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QLIB_DATA_DIR=/data/qlib \
    QUANT_ARTIFACTS_DIR=/data/artifacts \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf \
        /usr/local/lib/python3.11/ensurepip \
        /usr/local/lib/python3.11/site-packages/pip* \
        /usr/local/lib/python3.11/site-packages/setuptools* \
        /usr/local/lib/python3.11/site-packages/wheel* \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11 \
    && groupadd --system --gid 10001 nexus \
    && useradd --system --uid 10001 --gid nexus --home-dir /nonexistent --shell /usr/sbin/nologin nexus \
    && mkdir -p /data/qlib /data/artifacts \
    && chown -R nexus:nexus /data

WORKDIR /app

COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

EXPOSE 8000

USER nexus

CMD ["uvicorn", "worker.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
