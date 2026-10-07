DO $$
DECLARE
    actor_table record;
BEGIN
    FOR actor_table IN
        SELECT schemaname, tablename
        FROM pg_policies
        WHERE schemaname = 'nexus_domain'
          AND policyname = 'nexus_actor_isolation'
    LOOP
        EXECUTE format(
            'DROP POLICY nexus_actor_isolation ON %I.%I',
            actor_table.schemaname,
            actor_table.tablename
        );
    END LOOP;
END
$$;

DROP FUNCTION nexus_domain.quant_deleted_artifacts(integer);
DROP FUNCTION nexus_domain.quant_expired_artifacts(integer);
DROP FUNCTION nexus_domain.quant_recovery_actors(integer);
DROP FUNCTION nexus_domain.quant_pending_experiments();
DROP FUNCTION nexus_domain.quant_active_experiment_count();
DROP FUNCTION nexus_domain.project_product_outbox(integer, integer);
DROP FUNCTION nexus_domain.actor_policy_allows(text);
