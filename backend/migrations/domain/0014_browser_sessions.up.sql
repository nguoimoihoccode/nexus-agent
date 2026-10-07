CREATE TABLE nexus_domain.oidc_login_transactions (
    state_hash bytea PRIMARY KEY,
    browser_binding_hash bytea NOT NULL,
    nonce_hash bytea NOT NULL,
    verifier_ciphertext bytea NOT NULL,
    return_to text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CONSTRAINT oidc_login_state_hash_shape CHECK (octet_length(state_hash) = 32),
    CONSTRAINT oidc_login_browser_binding_hash_shape CHECK (octet_length(browser_binding_hash) = 32),
    CONSTRAINT oidc_login_nonce_hash_shape CHECK (octet_length(nonce_hash) = 32),
    CONSTRAINT oidc_login_verifier_shape CHECK (octet_length(verifier_ciphertext) >= 29),
    CONSTRAINT oidc_login_return_to_shape CHECK (
        length(return_to) BETWEEN 1 AND 2048
        AND left(return_to, 1) = '/'
        AND left(return_to, 2) <> '//'
        AND position(chr(92) in return_to) = 0
        AND return_to !~ '[[:cntrl:]]'
    ),
    CONSTRAINT oidc_login_duration CHECK (
        expires_at > created_at
        AND expires_at <= created_at + interval '10 minutes'
    )
);

CREATE INDEX oidc_login_transactions_expiry_idx
    ON nexus_domain.oidc_login_transactions (expires_at);

CREATE TABLE nexus_domain.browser_sessions (
    session_hash bytea PRIMARY KEY,
    actor_key text NOT NULL,
    permissions text[] NOT NULL,
    credential_ciphertext bytea NOT NULL,
    token_expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    idle_expires_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    revocation_reason text,
    CONSTRAINT browser_session_hash_shape CHECK (octet_length(session_hash) = 32),
    CONSTRAINT browser_session_actor_shape CHECK (actor_key ~ '^v1-[0-9a-f]{64}$'),
    CONSTRAINT browser_session_credentials_shape CHECK (octet_length(credential_ciphertext) >= 29),
    CONSTRAINT browser_session_permissions_shape CHECK (cardinality(permissions) BETWEEN 1 AND 64),
    CONSTRAINT browser_session_duration CHECK (
        idle_expires_at > created_at
        AND expires_at > created_at
        AND idle_expires_at <= expires_at
        AND expires_at <= created_at + interval '8 hours'
    ),
    CONSTRAINT browser_session_revocation_shape CHECK (
        (revoked_at IS NULL AND revocation_reason IS NULL)
        OR (revoked_at IS NOT NULL AND revocation_reason IS NOT NULL)
    )
);

CREATE INDEX browser_sessions_actor_idx
    ON nexus_domain.browser_sessions (actor_key, expires_at DESC);

CREATE INDEX browser_sessions_expiry_idx
    ON nexus_domain.browser_sessions (idle_expires_at, expires_at)
    WHERE revoked_at IS NULL;

REVOKE ALL ON nexus_domain.oidc_login_transactions FROM PUBLIC;
REVOKE ALL ON nexus_domain.browser_sessions FROM PUBLIC;
