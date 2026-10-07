DROP INDEX IF EXISTS nexus_domain.experiments_recovery_idx;
ALTER TABLE nexus_domain.experiments
    DROP CONSTRAINT IF EXISTS experiments_idempotency_unique,
    DROP COLUMN IF EXISTS attempt_limit,
    DROP COLUMN IF EXISTS arguments_digest,
    DROP COLUMN IF EXISTS idempotency_key;
