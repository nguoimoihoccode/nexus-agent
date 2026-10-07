DROP INDEX IF EXISTS nexus_domain.publication_effects_evidence_idx;
DROP INDEX IF EXISTS nexus_domain.publication_effects_run_idx;
DROP INDEX IF EXISTS nexus_domain.approval_requests_run_idx;

ALTER TABLE nexus_domain.publication_effects
    DROP COLUMN IF EXISTS evidence_references,
    DROP COLUMN IF EXISTS run_id,
    DROP COLUMN IF EXISTS thread_id;

ALTER TABLE nexus_domain.approval_requests
    DROP CONSTRAINT IF EXISTS approval_execution_shape_check,
    DROP COLUMN IF EXISTS execution_error,
    DROP COLUMN IF EXISTS execution_reference,
    DROP COLUMN IF EXISTS executed_at,
    DROP COLUMN IF EXISTS execution_status,
    DROP COLUMN IF EXISTS decided_by_actor,
    DROP COLUMN IF EXISTS sensitivity,
    DROP COLUMN IF EXISTS human_summary,
    DROP COLUMN IF EXISTS action_schema_version,
    DROP COLUMN IF EXISTS tool_call_id,
    DROP COLUMN IF EXISTS agent_name;
