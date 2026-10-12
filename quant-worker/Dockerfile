FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv

FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS builder

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

FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS runtime

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
