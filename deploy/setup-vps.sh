#!/usr/bin/env bash
#
# One-time host setup for the Nexus demo. Run this ON the host, as root.
#
# Copy it over and run it with an allocated terminal:
#
#   scp deploy/setup-vps.sh root@<host>:/tmp/
#   ssh -t root@<host> 'bash /tmp/setup-vps.sh'
#
# Do not use `ssh root@<host> 'bash -s' < deploy/setup-vps.sh`. The script
# arrives on stdin, and the prompts below read from stdin too, so `read` would
# swallow the rest of the script instead of reading your answer. The `-t` above
# gives the prompts a terminal and lets htpasswd ask for the password itself.
#
# It is idempotent and additive: it never edits the other vhosts already served
# by this nginx, only adds a new server block for the demo hostname.
#
# Non-interactive use: set NEXUS_DEPLOY_PUBKEY and NEXUS_BASIC_AUTH_USER to
# answer the first two prompts, and pipe the htpasswd password in when
# NEXUS_BASIC_AUTH_PASSWORD is set.
#
# After it finishes you have to add four values to GitHub before the deploy
# workflow can run. The script prints the exact list at the end.

set -euo pipefail

APP_DIR="${NEXUS_APP_DIR:-/opt/nexus/nexus-agent}"
REPO_URL="${NEXUS_REPO_URL:-https://github.com/nguoimoihoccode/nexus-agent.git}"
DOMAIN="${NEXUS_DOMAIN:-nguoimoihoccode.io.vn}"
DEPLOY_USER="${NEXUS_DEPLOY_USER:-deploy}"
HTPASSWD_FILE="/etc/nginx/nexus-demo.htpasswd"
ACME_WEBROOT="/var/www/certbot"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VHOST_SOURCE="${SCRIPT_DIR}/nginx/nguoimoihoccode.io.vn.conf"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"
command -v docker >/dev/null || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is not installed"
command -v nginx >/dev/null || die "nginx is not installed"

# ---------------------------------------------------------------------------
log "Creating the ${DEPLOY_USER} account"
# The deploy account owns the checkout and can drive Docker, but has no sudo.
# The workflow authenticates as this user with a dedicated key, so the root
# password is never part of a deployment.
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --system --group --home "/home/${DEPLOY_USER}" --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh"
install -m 700 -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh"

if [ ! -s "/home/${DEPLOY_USER}/.ssh/authorized_keys" ]; then
  if [ -n "${NEXUS_DEPLOY_PUBKEY:-}" ]; then
    deploy_pubkey="$NEXUS_DEPLOY_PUBKEY"
  else
    printf '%s\n' \
      "No authorized_keys yet for ${DEPLOY_USER}." \
      "Generate the deploy key locally, then paste the PUBLIC key here:"
    read -r -p "public key: " deploy_pubkey
  fi
  case "$deploy_pubkey" in
    ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*\ *) ;;
    *) die "that does not look like an SSH public key" ;;
  esac
  printf '%s\n' "$deploy_pubkey" \
    > "/home/${DEPLOY_USER}/.ssh/authorized_keys"
fi
chmod 600 "/home/${DEPLOY_USER}/.ssh/authorized_keys"
chown "$DEPLOY_USER:$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh/authorized_keys"

# ---------------------------------------------------------------------------
log "Preparing the checkout at ${APP_DIR}"
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$(dirname "$APP_DIR")"
if [ ! -d "${APP_DIR}/.git" ]; then
  sudo -u "$DEPLOY_USER" git clone "$REPO_URL" "$APP_DIR"
fi
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
chmod 700 "$APP_DIR"

# The workflow stages the environment here and remote-deploy.sh consumes it.
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}"

# ---------------------------------------------------------------------------
log "Installing the nginx ingress for ${DOMAIN}"
command -v htpasswd >/dev/null || die "htpasswd is missing (install apache2-utils)"

# Detect which directory this nginx actually loads. Adding a file to a
# directory that is not included silently does nothing.
if grep -qE '^\s*include\s+/etc/nginx/conf\.d/\*\.conf;' /etc/nginx/nginx.conf; then
  NGINX_DIR=/etc/nginx/conf.d
elif [ -d /etc/nginx/sites-enabled ]; then
  NGINX_DIR=/etc/nginx/sites-enabled
else
  die "cannot find where nginx loads vhosts; install the vhost by hand"
fi
VHOST_TARGET="${NGINX_DIR}/${DOMAIN}.conf"
log "vhost directory: ${NGINX_DIR}"

install -d -m 755 "$ACME_WEBROOT"

install_bootstrap_vhost() {
  # Port 80 only. The full vhost references certificate files that do not exist
  # until certbot has run, so nginx -t would reject it before then.
  cat > "$VHOST_TARGET" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root ${ACME_WEBROOT};
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}
EOF
}

if [ -s "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
  log "certificate already present"
  install -m 644 "$VHOST_SOURCE" "$VHOST_TARGET"
else
  log "running the ACME challenge to obtain a certificate"
  install_bootstrap_vhost
  nginx -t
  systemctl reload nginx

  command -v certbot >/dev/null || die "certbot is missing; install it, then re-run"
  certbot certonly --webroot --webroot-path "$ACME_WEBROOT" \
    --non-interactive --agree-tos --register-unsafely-without-email \
    -d "$DOMAIN" \
    || die "certbot failed; check that ${DOMAIN} resolves here and port 80 is open"

  install -m 644 "$VHOST_SOURCE" "$VHOST_TARGET"
fi

if [ ! -s "$HTPASSWD_FILE" ]; then
  log "Setting the basic-auth credential for the demo gate"
  if [ -n "${NEXUS_BASIC_AUTH_USER:-}" ]; then
    basic_auth_user="$NEXUS_BASIC_AUTH_USER"
  else
    read -r -p "basic-auth username: " basic_auth_user
  fi
  [ -n "$basic_auth_user" ] || die "username cannot be empty"
  if [ -n "${NEXUS_BASIC_AUTH_PASSWORD:-}" ]; then
    # -i reads the password from stdin, so it never reaches an argument list.
    printf '%s\n' "$NEXUS_BASIC_AUTH_PASSWORD" |
      htpasswd -i -c "$HTPASSWD_FILE" "$basic_auth_user"
    unset NEXUS_BASIC_AUTH_PASSWORD
  else
    # -c creates the file; htpasswd prompts on the terminal and reads it back,
    # so the password is never echoed.
    htpasswd -c "$HTPASSWD_FILE" "$basic_auth_user"
  fi
fi
chmod 640 "$HTPASSWD_FILE"
chgrp www-data "$HTPASSWD_FILE" 2>/dev/null || true

nginx -t
systemctl reload nginx

# ---------------------------------------------------------------------------
log "Checking the frontend bind address"
printf '%s\n' \
  "The frontend container publishes to 127.0.0.1:8080. If that port is already" \
  "taken on this host, set APP_PORT in the deployment environment file."

log "Setup complete"
cat <<EOF

Add these to GitHub before the first deploy:

  Repository -> Settings -> Environments -> New environment -> "demo"
    Secrets
      VPS_SSH_KEY               private half of the deploy key
      DEMO_ENV                  contents of deploy/.env.example, filled in
      DEMO_BASIC_AUTH_USER      the basic-auth username chosen above
      DEMO_BASIC_AUTH_PASSWORD  the matching password
    Variables
      VPS_HOST                  116.118.6.139
      VPS_USER                  ${DEPLOY_USER}
      VPS_KNOWN_HOSTS           output of: ssh-keyscan -H 116.118.6.139

Generate the deploy key with:
  ssh-keygen -t ed25519 -C nexus-deploy -f ~/.ssh/nexus_deploy -N ''

Generate AI_TRADER_TOKEN for DEMO_ENV with:
  openssl rand -hex 32
EOF