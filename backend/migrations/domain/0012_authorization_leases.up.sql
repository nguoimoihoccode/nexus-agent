CREATE TABLE nexus_domain.authorization_leases (
    actor_key text NOT NULL,
    lease_id text NOT NULL,
    thread_id text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('autonomous', 'full_access')),
    allow_sensitive boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    revocation_reason text,
    PRIMARY KEY (actor_key, lease_id),
    CONSTRAINT authorization_lease_id_shape CHECK (
        lease_id ~ '^azl_v1_[0-9a-f]{32}$'
    ),
    CONSTRAINT authorization_lease_thread_shape CHECK (
        thread_id ~ '^[A-Za-z0-9._:-]{1,200}$'
    ),
    CONSTRAINT authorization_lease_duration CHECK (
        expires_at > created_at
        AND expires_at <= created_at + interval '4 hours'
    ),
    CONSTRAINT authorization_lease_sensitive_mode CHECK (
        mode = 'full_access' OR allow_sensitive = false
    ),
    CONSTRAINT authorization_lease_revocation_shape CHECK (
        (revoked_at IS NULL AND revocation_reason IS NULL)
        OR (revoked_at IS NOT NULL AND revocation_reason IS NOT NULL)
    )
);

CREATE UNIQUE INDEX authorization_leases_active_thread_idx
    ON nexus_domain.authorization_leases (actor_key, thread_id)
    WHERE revoked_at IS NULL;

CREATE INDEX authorization_leases_lookup_idx
    ON nexus_domain.authorization_leases
        (actor_key, thread_id, expires_at DESC);

CREATE POLICY nexus_actor_isolation
ON nexus_domain.authorization_leases
FOR ALL TO PUBLIC
USING (nexus_domain.actor_policy_allows(actor_key))
WITH CHECK (nexus_domain.actor_policy_allows(actor_key));

ALTER TABLE nexus_domain.authorization_leases ENABLE ROW LEVEL SECURITY;
