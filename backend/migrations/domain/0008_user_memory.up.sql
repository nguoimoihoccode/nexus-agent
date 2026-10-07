CREATE TABLE nexus_domain.user_memories (
    actor_key text PRIMARY KEY,
    content text NOT NULL,
    content_hash text NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT user_memories_content_size
        CHECK (octet_length(content) BETWEEN 1 AND 16384),
    CONSTRAINT user_memories_revision_positive CHECK (revision > 0)
);
