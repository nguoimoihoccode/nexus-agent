ALTER TABLE nexus_domain.approval_requests
    DROP CONSTRAINT approval_requests_actor_key_action_digest_key,
    ADD COLUMN expires_at timestamptz;

UPDATE nexus_domain.approval_requests
SET expires_at = requested_at + interval '15 minutes'
WHERE expires_at IS NULL;

ALTER TABLE nexus_domain.approval_requests
    ALTER COLUMN expires_at SET NOT NULL;

CREATE UNIQUE INDEX approval_requests_active_action_idx
    ON nexus_domain.approval_requests (actor_key, action_digest)
    WHERE status IN ('pending', 'approved', 'rejected');

CREATE INDEX approval_requests_expiry_idx
    ON nexus_domain.approval_requests (expires_at)
    WHERE status = 'pending';

ALTER TABLE nexus_domain.product_events
    ADD CONSTRAINT product_events_schema_version_check
    CHECK (schema_version = 1) NOT VALID,
    ADD CONSTRAINT product_events_sensitivity_check
    CHECK (sensitivity IN ('public', 'internal', 'restricted')) NOT VALID,
    ADD CONSTRAINT product_events_payload_object_check
    CHECK (jsonb_typeof(payload) = 'object') NOT VALID,
    ADD CONSTRAINT product_events_type_registry_check
    CHECK (event_type IN (
        'agent.delegated',
        'approval.decided',
        'approval.expired',
        'approval.requested',
        'artifact.created',
        'artifact.deleted',
        'artifact.expired',
        'dataset.ready',
        'factor_snapshot.ready',
        'memory.updated',
        'publication.published',
        'run.completed',
        'run.failed',
        'run.started',
        'staging.ready',
        'tool.completed',
        'tool.failed',
        'tool.proposed',
        'workflow.transitioned',
        'worker.accepted',
        'worker.cancelled',
        'worker.cancellation_requested',
        'worker.completed',
        'worker.expired',
        'worker.failed',
        'worker.leased',
        'worker.requeued',
        'worker.started'
    )) NOT VALID;
