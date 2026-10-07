ALTER TABLE nexus_domain.experiments
    ADD COLUMN idempotency_key text,
    ADD COLUMN arguments_digest text,
    ADD COLUMN attempt_limit integer NOT NULL DEFAULT 3 CHECK (attempt_limit > 0);

UPDATE nexus_domain.experiments
SET idempotency_key = 'legacy:' || experiment_id,
    arguments_digest = specification_digest
WHERE idempotency_key IS NULL;

ALTER TABLE nexus_domain.experiments
    ALTER COLUMN idempotency_key SET NOT NULL,
    ALTER COLUMN arguments_digest SET NOT NULL;

ALTER TABLE nexus_domain.experiments
    ADD CONSTRAINT experiments_idempotency_unique
    UNIQUE (actor_key, idempotency_key);

CREATE INDEX experiments_recovery_idx
    ON nexus_domain.experiments (status, lease_expires_at, updated_at)
    WHERE status IN ('queued', 'leased', 'running', 'cancelling');
