FROM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS build

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

FROM nginx:1.31-alpine@sha256:df221db836e1754089190208cee7eeda94f233197056426eda74a43ab1abeac2 AS runtime

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
