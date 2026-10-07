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
            'ALTER TABLE nexus_domain.%I DISABLE ROW LEVEL SECURITY',
            actor_table.table_name
        );
    END LOOP;
END
$$;
