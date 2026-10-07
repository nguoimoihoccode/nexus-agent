CREATE TABLE IF NOT EXISTS nexus_domain.user_memory_items (
    actor_key text NOT NULL,
    memory_key text NOT NULL,
    content text NOT NULL,
    content_hash text NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, memory_key),
    FOREIGN KEY (actor_key)
        REFERENCES nexus_domain.user_memories (actor_key)
        ON DELETE CASCADE,
    CONSTRAINT user_memory_items_key_shape CHECK (
        memory_key ~ '^[a-z0-9][a-z0-9._-]{0,127}$'
    ),
    CONSTRAINT user_memory_items_content_size CHECK (
        octet_length(content) BETWEEN 1 AND 16384
    ),
    CONSTRAINT user_memory_items_revision_positive CHECK (revision > 0)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'nexus_domain'
          AND tablename = 'user_memory_items'
          AND policyname = 'nexus_actor_isolation'
    ) THEN
        CREATE POLICY nexus_actor_isolation
        ON nexus_domain.user_memory_items
        FOR ALL TO PUBLIC
        USING (nexus_domain.actor_policy_allows(actor_key))
        WITH CHECK (nexus_domain.actor_policy_allows(actor_key));
    END IF;
END
$$;

ALTER TABLE nexus_domain.user_memory_items ENABLE ROW LEVEL SECURITY;
