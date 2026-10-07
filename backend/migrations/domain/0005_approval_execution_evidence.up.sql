ALTER TABLE nexus_domain.approval_requests
    ADD COLUMN agent_name text NOT NULL DEFAULT 'supervisor',
    ADD COLUMN tool_call_id text NOT NULL DEFAULT 'legacy',
    ADD COLUMN action_schema_version text NOT NULL DEFAULT '1',
    ADD COLUMN human_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN sensitivity text NOT NULL DEFAULT 'restricted'
        CHECK (sensitivity IN ('internal', 'restricted')),
    ADD COLUMN decided_by_actor text,
    ADD COLUMN execution_status text NOT NULL DEFAULT 'not_executed'
        CHECK (execution_status IN ('not_executed', 'executed', 'failed')),
    ADD COLUMN executed_at timestamptz,
    ADD COLUMN execution_reference jsonb,
    ADD COLUMN execution_error jsonb;

ALTER TABLE nexus_domain.publication_effects
    ADD COLUMN thread_id text,
    ADD COLUMN run_id text,
    ADD COLUMN evidence_references jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE nexus_domain.approval_requests
    ADD CONSTRAINT approval_execution_shape_check CHECK (
        (execution_status = 'not_executed' AND executed_at IS NULL)
        OR (execution_status IN ('executed', 'failed') AND executed_at IS NOT NULL)
    ) NOT VALID;

CREATE INDEX approval_requests_run_idx
    ON nexus_domain.approval_requests (actor_key, run_id, requested_at);

CREATE INDEX publication_effects_run_idx
    ON nexus_domain.publication_effects (actor_key, run_id, created_at);

CREATE INDEX publication_effects_evidence_idx
    ON nexus_domain.publication_effects USING gin (evidence_references);
