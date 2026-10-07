CREATE FUNCTION nexus_domain.actor_policy_allows(row_actor text)
RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT row_actor = NULLIF(current_setting('nexus.actor_key', true), '')
        AND (
            pg_has_role(current_user, 'nexus_backend_role', 'member')
            OR pg_has_role(current_user, 'nexus_quant_data_role', 'member')
            OR pg_has_role(current_user, 'nexus_quant_worker_role', 'member')
        )
$$;

CREATE FUNCTION nexus_domain.project_product_outbox(
    retention_seconds integer,
    requested_limit integer
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, nexus_domain
AS $$
DECLARE
    item record;
    projected integer := 0;
    expires_at timestamptz := now() + make_interval(
        secs => greatest(1, retention_seconds)
    );
BEGIN
    IF NOT pg_has_role(session_user, 'nexus_backend_role', 'member') THEN
        RAISE EXCEPTION 'backend projector role is required' USING ERRCODE = '42501';
    END IF;
    FOR item IN
        SELECT o.actor_key, o.outbox_id, o.event_id,
               e.run_id, e.sequence, e.event_type,
               e.schema_version, e.thread_id, e.sensitivity,
               e.payload, e.occurred_at
        FROM nexus_domain.transactional_outbox o
        JOIN nexus_domain.product_events e
          ON e.actor_key = o.actor_key AND e.event_id = o.event_id
        WHERE o.published_at IS NULL
        ORDER BY o.created_at, o.outbox_id
        LIMIT greatest(1, least(requested_limit, 1000))
        FOR UPDATE OF o SKIP LOCKED
    LOOP
        INSERT INTO nexus_domain.replay_events
            (actor_key, run_id, sequence, event_id, event_type, payload, expires_at)
        VALUES (
            item.actor_key,
            item.run_id,
            item.sequence,
            item.event_id,
            item.event_type,
            jsonb_build_object(
                'event_id', item.event_id,
                'schema_version', item.schema_version,
                'sequence', item.sequence,
                'actor_key', item.actor_key,
                'thread_id', item.thread_id,
                'run_id', item.run_id,
                'event_type', item.event_type,
                'occurred_at', item.occurred_at,
                'sensitivity', item.sensitivity,
                'payload', item.payload
            ),
            expires_at
        )
        ON CONFLICT (actor_key, event_id) DO NOTHING;

        UPDATE nexus_domain.transactional_outbox
        SET published_at = now(), attempts = attempts + 1
        WHERE actor_key = item.actor_key AND outbox_id = item.outbox_id;
        projected := projected + 1;
    END LOOP;

    DELETE FROM nexus_domain.replay_events
    WHERE replay_events.expires_at <= now();
    RETURN projected;
END
$$;

CREATE FUNCTION nexus_domain.quant_active_experiment_count()
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, nexus_domain
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'nexus_quant_worker_role', 'member') THEN
        RAISE EXCEPTION 'quant worker role is required' USING ERRCODE = '42501';
    END IF;
    RETURN (
        SELECT count(*)
        FROM nexus_domain.experiments
        WHERE status IN ('queued', 'leased', 'running', 'cancelling')
    );
END
$$;

CREATE FUNCTION nexus_domain.quant_pending_experiments()
RETURNS TABLE(actor_key text, experiment_id text)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, nexus_domain
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'nexus_quant_worker_role', 'member') THEN
        RAISE EXCEPTION 'quant worker role is required' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT experiments.actor_key, experiments.experiment_id
        FROM nexus_domain.experiments
        WHERE status = 'queued'
        ORDER BY created_at;
END
$$;

CREATE FUNCTION nexus_domain.quant_recovery_actors(queued_ttl_seconds integer)
RETURNS TABLE(actor_key text)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, nexus_domain
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'nexus_quant_worker_role', 'member') THEN
        RAISE EXCEPTION 'quant worker role is required' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT DISTINCT experiments.actor_key
        FROM nexus_domain.experiments
        WHERE (
            status = 'queued'
            AND created_at < now() - make_interval(
                secs => greatest(1, queued_ttl_seconds)
            )
        ) OR (
            status IN ('leased', 'running', 'cancelling')
            AND (lease_expires_at IS NULL OR lease_expires_at < now())
        )
        ORDER BY experiments.actor_key;
END
$$;

CREATE FUNCTION nexus_domain.quant_expired_artifacts(requested_limit integer)
RETURNS TABLE(actor_key text, artifact_id text)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, nexus_domain
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'nexus_quant_worker_role', 'member') THEN
        RAISE EXCEPTION 'quant worker role is required' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT artifacts.actor_key, artifacts.artifact_id
        FROM nexus_domain.artifacts
        WHERE deleted_at IS NULL AND expires_at IS NOT NULL
          AND expires_at <= now()
        ORDER BY artifacts.expires_at, artifacts.artifact_id
        LIMIT greatest(1, least(requested_limit, 1000));
END
$$;

CREATE FUNCTION nexus_domain.quant_deleted_artifacts(requested_limit integer)
RETURNS TABLE(actor_key text, storage_key text)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, nexus_domain
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'nexus_quant_worker_role', 'member') THEN
        RAISE EXCEPTION 'quant worker role is required' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT artifacts.actor_key, artifacts.storage_key
        FROM nexus_domain.artifacts
        WHERE deleted_at IS NOT NULL
        ORDER BY artifacts.deleted_at DESC, artifacts.artifact_id
        LIMIT greatest(1, least(requested_limit, 5000));
END
$$;

REVOKE ALL ON FUNCTION nexus_domain.project_product_outbox(integer, integer)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION nexus_domain.quant_active_experiment_count() FROM PUBLIC;
REVOKE ALL ON FUNCTION nexus_domain.quant_pending_experiments() FROM PUBLIC;
REVOKE ALL ON FUNCTION nexus_domain.quant_recovery_actors(integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION nexus_domain.quant_expired_artifacts(integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION nexus_domain.quant_deleted_artifacts(integer) FROM PUBLIC;

DO $$
DECLARE
    actor_table record;
BEGIN
    FOR actor_table IN
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'nexus_domain' AND column_name = 'actor_key'
        ORDER BY table_name
    LOOP
        EXECUTE format(
            'CREATE POLICY nexus_actor_isolation ON nexus_domain.%I '
            'FOR ALL TO PUBLIC '
            'USING (nexus_domain.actor_policy_allows(actor_key)) '
            'WITH CHECK (nexus_domain.actor_policy_allows(actor_key))',
            actor_table.table_name
        );
    END LOOP;
END
$$;
