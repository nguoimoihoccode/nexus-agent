-- Run as a database owner/administrator after domain migrations. These are
-- group roles; deployment-specific login roles inherit exactly one group role.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_backend_role') THEN
        CREATE ROLE nexus_backend_role NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_quant_data_role') THEN
        CREATE ROLE nexus_quant_data_role NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexus_quant_worker_role') THEN
        CREATE ROLE nexus_quant_worker_role NOLOGIN;
    END IF;
END
$$;

REVOKE ALL ON SCHEMA langgraph FROM PUBLIC;
REVOKE ALL ON SCHEMA nexus_domain FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA langgraph TO nexus_backend_role;
GRANT USAGE ON SCHEMA nexus_domain TO
    nexus_backend_role, nexus_quant_data_role, nexus_quant_worker_role;

GRANT SELECT, INSERT, UPDATE ON
    nexus_domain.idempotency_decisions,
    nexus_domain.approval_requests,
    nexus_domain.authorization_leases,
    nexus_domain.product_events,
    nexus_domain.transactional_outbox,
    nexus_domain.lineage_nodes,
    nexus_domain.lineage_edges,
    nexus_domain.research_sources,
    nexus_domain.artifacts,
    nexus_domain.artifact_tombstones,
    nexus_domain.workflows,
    nexus_domain.workflow_transitions,
    nexus_domain.replay_events,
    nexus_domain.publication_effects,
    nexus_domain.interpretation_reports,
    nexus_domain.dossier_exports,
    nexus_domain.user_memories,
    nexus_domain.user_memory_items,
    nexus_domain.oidc_login_transactions,
    nexus_domain.browser_sessions
TO nexus_backend_role;
GRANT SELECT ON
    nexus_domain.dataset_revisions,
    nexus_domain.dataset_manifest_files,
    nexus_domain.experiments,
    nexus_domain.job_attempts
TO nexus_backend_role;
GRANT DELETE ON nexus_domain.replay_events TO nexus_backend_role;
GRANT DELETE ON nexus_domain.user_memories TO nexus_backend_role;
GRANT DELETE ON
    nexus_domain.oidc_login_transactions,
    nexus_domain.browser_sessions
TO nexus_backend_role;
GRANT SELECT ON nexus_domain.schema_migrations TO nexus_backend_role;

GRANT SELECT, INSERT, UPDATE ON
    nexus_domain.request_fingerprints,
    nexus_domain.staging_revisions,
    nexus_domain.dataset_revisions,
    nexus_domain.dataset_aliases,
    nexus_domain.dataset_manifest_files,
    nexus_domain.product_events,
    nexus_domain.transactional_outbox,
    nexus_domain.lineage_nodes,
    nexus_domain.lineage_edges,
    nexus_domain.replay_events,
    nexus_domain.idempotency_decisions
TO nexus_quant_data_role;
GRANT SELECT ON nexus_domain.schema_migrations TO nexus_quant_data_role;

GRANT SELECT ON
    nexus_domain.dataset_revisions,
    nexus_domain.dataset_manifest_files
TO nexus_quant_worker_role;
GRANT SELECT ON nexus_domain.schema_migrations TO nexus_quant_worker_role;
GRANT SELECT, INSERT, UPDATE ON
    nexus_domain.experiments,
    nexus_domain.job_attempts,
    nexus_domain.idempotency_decisions,
    nexus_domain.artifacts,
    nexus_domain.artifact_tombstones,
    nexus_domain.product_events,
    nexus_domain.transactional_outbox,
    nexus_domain.lineage_nodes,
    nexus_domain.lineage_edges,
    nexus_domain.replay_events
TO nexus_quant_worker_role;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA nexus_domain TO
    nexus_backend_role, nexus_quant_data_role, nexus_quant_worker_role;

GRANT EXECUTE ON FUNCTION nexus_domain.project_product_outbox(integer, integer)
TO nexus_backend_role;
GRANT EXECUTE ON FUNCTION nexus_domain.quant_active_experiment_count(),
    nexus_domain.quant_pending_experiments(),
    nexus_domain.quant_recovery_actors(integer),
    nexus_domain.quant_expired_artifacts(integer),
    nexus_domain.quant_deleted_artifacts(integer)
TO nexus_quant_worker_role;
