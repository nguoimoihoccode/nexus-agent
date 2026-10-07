ALTER TABLE nexus_domain.product_events
    DROP CONSTRAINT IF EXISTS product_events_type_registry_check,
    DROP CONSTRAINT IF EXISTS product_events_payload_object_check,
    DROP CONSTRAINT IF EXISTS product_events_sensitivity_check,
    DROP CONSTRAINT IF EXISTS product_events_schema_version_check;

DROP INDEX IF EXISTS nexus_domain.approval_requests_expiry_idx;
DROP INDEX IF EXISTS nexus_domain.approval_requests_active_action_idx;

ALTER TABLE nexus_domain.approval_requests
    DROP COLUMN IF EXISTS expires_at,
    ADD CONSTRAINT approval_requests_actor_key_action_digest_key
        UNIQUE (actor_key, action_digest);
