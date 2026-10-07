FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci \
    --no-audit \
    --no-fund \
    --fetch-retries=5 \
    --fetch-retry-mintimeout=20000 \
    --fetch-retry-maxtimeout=120000 \
    --fetch-timeout=300000

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10 AS runtime

ARG NGINX_BACKEND_UPSTREAM=http://backend:2024
ENV NGINX_BACKEND_UPSTREAM=${NGINX_BACKEND_UPSTREAM}

RUN --mount=type=secret,id=host_ca,target=/tmp/host-ca-certificates.crt \
    if [ -s /tmp/host-ca-certificates.crt ]; then \
        SSL_CERT_FILE=/tmp/host-ca-certificates.crt apk upgrade --no-cache; \
    else \
        apk upgrade --no-cache; \
    fi

COPY frontend/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/frontend/dist /usr/share/nginx/html

RUN sed -i '/^user /d' /etc/nginx/nginx.conf \
    && sed -i 's!^pid .*;!pid /tmp/nginx.pid;!' /etc/nginx/nginx.conf \
    && chown -R nginx:nginx \
        /etc/nginx/conf.d \
        /var/cache/nginx

USER nginx

EXPOSE 8080
