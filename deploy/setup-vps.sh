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
# NEXUS_BASIC_AUTH_PASSWORD is set. Supplying NEXUS_DEPLOY_PUBKEY is also how a
# re-run repairs a host whose authorized_keys holds a different key: the key is
# appended when missing, so the deploy can authenticate afterwards.
#
# After it finishes you have to add the values it prints to GitHub before the
# deploy workflow can run. The list at the end names every one of them and
# says which scope each belongs to.

set -euo pipefail

APP_DIR="${NEXUS_APP_DIR:-/opt/nexus/nexus-agent}"
REPO_URL="${NEXUS_REPO_URL:-https://github.com/nguoimoihoccode/nexus-agent.git}"
DOMAIN="${NEXUS_DOMAIN:-nguoimoihoccode.io.vn}"
DEPLOY_USER="${NEXUS_DEPLOY_USER:-deploy}"
HTPASSWD_FILE="/etc/nginx/nexus-demo.htpasswd"
ACME_WEBROOT="/var/www/certbot"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Run a command as the deploy account. runuser ships with util-linux on every
# supported host; sudo is the fallback for images that strip it.
as_deploy() {
  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$DEPLOY_USER" -- "$@"
  else
    sudo -u "$DEPLOY_USER" "$@"
  fi
}

[ "$(id -u)" -eq 0 ] || die "run as root"
command -v docker >/dev/null || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is not installed"
command -v nginx >/dev/null || die "nginx is not installed"
# Required unconditionally, so it belongs here rather than where it is used.
# Everything between this point and that use creates the deploy account and
# clones the checkout; failing there leaves real work behind over a package
# that installs in seconds.
command -v htpasswd >/dev/null || die "htpasswd is missing (install apache2-utils)"

# ---------------------------------------------------------------------------
log "Creating the ${DEPLOY_USER} account"
# The deploy account owns the checkout and can drive Docker, but has no sudo.
# The workflow authenticates as this user with a dedicated key, so the root
# password is never part of a deployment.
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --system --group --home "/home/${DEPLOY_USER}" --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh"
AUTHORIZED_KEYS="/home/${DEPLOY_USER}/.ssh/authorized_keys"

# Resolve the key to authorize. NEXUS_DEPLOY_PUBKEY answers this without a
# terminal; otherwise ask, but only when the host trusts nothing yet, so a
# re-run against a configured host stays quiet.
deploy_pubkey="${NEXUS_DEPLOY_PUBKEY:-}"
if [ -z "$deploy_pubkey" ] && [ ! -s "$AUTHORIZED_KEYS" ]; then
  printf '%s\n' \
    "No authorized_keys yet for ${DEPLOY_USER}." \
    "Generate the deploy key locally, then paste the PUBLIC key here:"
  read -r -p "public key: " deploy_pubkey
fi

if [ -n "$deploy_pubkey" ]; then
  case "$deploy_pubkey" in
    ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*\ *) ;;
    *) die "that does not look like an SSH public key" ;;
  esac
  # Match on the key material alone. The trailing comment is free-form and
  # differs between a key file and a pasted copy, so comparing whole lines
  # would append a second copy of a key that is already authorized.
  key_material="$(printf '%s\n' "$deploy_pubkey" | awk '{print $1" "$2}')"
  present="$(awk '{print $1" "$2}' "$AUTHORIZED_KEYS" 2>/dev/null |
    grep -xF "$key_material" || true)"
  if [ -n "$present" ]; then
    log "the supplied key is already authorized for ${DEPLOY_USER}"
  else
    # Appended rather than written, and added when missing rather than only
    # when the file is empty. The old version wrote this file only when
    # nothing was authorized, so a host holding the wrong key kept it and the
    # deploy failed later with "Permission denied (publickey)" -- a red run
    # reported far from its cause. Appending is also what lets a re-run with
    # NEXUS_DEPLOY_PUBKEY repair such a host.
    printf '%s\n' "$deploy_pubkey" >> "$AUTHORIZED_KEYS"
    log "authorized the supplied key for ${DEPLOY_USER}"
  fi
fi

chmod 600 "$AUTHORIZED_KEYS"
chown "$DEPLOY_USER:$DEPLOY_USER" "$AUTHORIZED_KEYS"

# Print what this host will actually accept. An authorized_keys file that does
# not list the deploy key is the entire reason a later run reports
# "Permission denied (publickey)", and nothing else surfaces the mismatch.
log "Keys authorized for ${DEPLOY_USER}"
ssh-keygen -lf "$AUTHORIZED_KEYS" || printf '  %s\n' \
  "(ssh-keygen is unavailable; compare authorized_keys by hand)"

# ---------------------------------------------------------------------------
log "Preparing the checkout at ${APP_DIR}"
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$(dirname "$APP_DIR")"
# Ownership comes first. A checkout left behind by an earlier root login is
# owned by root, and git refuses to read a repository whose owner differs from
# the user running it ("detected dubious ownership"), which is its protection
# against a planted repo being executed through a privileged git.
if [ -e "$APP_DIR" ]; then
  chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
fi
if [ ! -d "${APP_DIR}/.git" ]; then
  as_deploy git clone "$REPO_URL" "$APP_DIR"
else
  # Pin the public HTTPS URL. The deploy account has no GitHub SSH key, so a
  # checkout that was originally cloned over SSH could not be fetched by it.
  as_deploy git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  as_deploy git -C "$APP_DIR" fetch --prune origin main
  as_deploy git -C "$APP_DIR" reset --hard origin/main
fi
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
chmod 700 "$APP_DIR"

# The vhost comes from the checkout, so this works when only this script was
# copied to the host. SCRIPT_DIR is the fallback for a run straight out of a
# local clone that has not been pushed yet.
VHOST_SOURCE="${APP_DIR}/deploy/nginx/${DOMAIN}.conf"
if [ ! -f "$VHOST_SOURCE" ]; then
  VHOST_SOURCE="${SCRIPT_DIR}/nginx/${DOMAIN}.conf"
fi
[ -f "$VHOST_SOURCE" ] || die "cannot find the vhost file for ${DOMAIN}"

# The workflow stages the environment here and remote-deploy.sh consumes it.
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}"

# ---------------------------------------------------------------------------
log "Installing the nginx ingress for ${DOMAIN}"

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
  # Checked before the bootstrap vhost below. That step writes an nginx config
  # and reloads the server, so dying after it would leave a host that is already
  # serving other sites pointed at a placeholder it never asked for.
  command -v certbot >/dev/null || die "certbot is missing; install it, then re-run"
  log "running the ACME challenge to obtain a certificate"
  install_bootstrap_vhost
  nginx -t
  systemctl reload nginx

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
# Also correct on a re-run, where the credential already existed.
basic_auth_user="${basic_auth_user:-$(cut -d: -f1 "$HTPASSWD_FILE" | head -n1)}"

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

  Repository -> Settings -> Secrets and variables -> Actions -> Variables
      DEPLOY_ENABLED            true

  Repository -> Settings -> Environments -> New environment -> "demo"
    Secrets
      VPS_SSH_KEY               private half of the deploy key
      DEMO_ENV                  contents of deploy/.env.example, filled in
      DEMO_BASIC_AUTH_USER      ${basic_auth_user}
      DEMO_BASIC_AUTH_PASSWORD  the matching password
    Variables
      VPS_HOST                  116.118.6.139
      VPS_USER                  ${DEPLOY_USER}
      VPS_KNOWN_HOSTS           output of: ssh-keyscan -H 116.118.6.139

DEPLOY_ENABLED is repository-scoped on purpose, not a typo. The deploy job's
gate runs before GitHub assigns the environment, so it cannot read an
environment variable. Putting this flag in "demo" leaves the deploy
permanently skipped and never explains why.

Generate the deploy key with:
  ssh-keygen -t ed25519 -C nexus-deploy -f ~/.ssh/nexus_deploy -N ''

Generate AI_TRADER_TOKEN for DEMO_ENV with:
  openssl rand -hex 32
EOF