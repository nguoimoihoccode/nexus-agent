CREATE SCHEMA IF NOT EXISTS langgraph;
CREATE SCHEMA IF NOT EXISTS nexus_domain;

CREATE TABLE nexus_domain.request_fingerprints (
    actor_key text NOT NULL,
    request_fingerprint text NOT NULL,
    namespace text NOT NULL,
    schema_version text NOT NULL,
    normalized_request jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, request_fingerprint)
);

CREATE TABLE nexus_domain.staging_revisions (
    actor_key text NOT NULL,
    staging_revision_id text NOT NULL,
    request_fingerprint text NOT NULL,
    content_hash text NOT NULL,
    schema_version text NOT NULL,
    metadata jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('ready', 'invalid', 'expired')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, staging_revision_id),
    UNIQUE (actor_key, content_hash),
    FOREIGN KEY (actor_key, request_fingerprint)
        REFERENCES nexus_domain.request_fingerprints
            (actor_key, request_fingerprint)
        ON DELETE RESTRICT
);

CREATE TABLE nexus_domain.dataset_revisions (
    actor_key text NOT NULL,
    dataset_revision_id text NOT NULL,
    source_staging_revision_id text NOT NULL,
    manifest_hash text NOT NULL,
    schema_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('building', 'ready', 'invalid', 'expired')),
    limitations jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    ready_at timestamptz,
    PRIMARY KEY (actor_key, dataset_revision_id),
    UNIQUE (actor_key, manifest_hash),
    FOREIGN KEY (actor_key, source_staging_revision_id)
        REFERENCES nexus_domain.staging_revisions
            (actor_key, staging_revision_id)
        ON DELETE RESTRICT
);

CREATE TABLE nexus_domain.dataset_aliases (
    actor_key text NOT NULL,
    dataset_alias text NOT NULL,
    dataset_revision_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, dataset_alias),
    FOREIGN KEY (actor_key, dataset_revision_id)
        REFERENCES nexus_domain.dataset_revisions
            (actor_key, dataset_revision_id)
        ON DELETE RESTRICT
);

CREATE TABLE nexus_domain.dataset_manifest_files (
    actor_key text NOT NULL,
    dataset_revision_id text NOT NULL,
    relative_path text NOT NULL,
    media_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    content_hash text NOT NULL,
    PRIMARY KEY (actor_key, dataset_revision_id, relative_path),
    FOREIGN KEY (actor_key, dataset_revision_id)
        REFERENCES nexus_domain.dataset_revisions
            (actor_key, dataset_revision_id)
        ON DELETE RESTRICT
);

CREATE TABLE nexus_domain.experiments (
    actor_key text NOT NULL,
    experiment_id text NOT NULL,
    dataset_revision_id text NOT NULL,
    specification_digest text NOT NULL,
    specification jsonb NOT NULL,
    status text NOT NULL CHECK (
        status IN (
            'queued', 'leased', 'running', 'cancelling', 'cancelled',
            'completed', 'failed', 'expired'
        )
    ),
    lease_owner text,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    progress_stage text,
    cancellation jsonb,
    result jsonb,
    error jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, experiment_id),
    FOREIGN KEY (actor_key, dataset_revision_id)
        REFERENCES nexus_domain.dataset_revisions
            (actor_key, dataset_revision_id)
        ON DELETE RESTRICT
);

CREATE INDEX experiments_claim_idx
    ON nexus_domain.experiments (status, lease_expires_at, created_at);
CREATE INDEX experiments_dataset_idx
    ON nexus_domain.experiments (actor_key, dataset_revision_id, created_at);

CREATE TABLE nexus_domain.job_attempts (
    actor_key text NOT NULL,
    experiment_id text NOT NULL,
    attempt integer NOT NULL CHECK (attempt > 0),
    lease_owner text,
    started_at timestamptz,
    finished_at timestamptz,
    status text NOT NULL,
    stage text,
    safe_error jsonb,
    runtime_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (actor_key, experiment_id, attempt),
    FOREIGN KEY (actor_key, experiment_id)
        REFERENCES nexus_domain.experiments (actor_key, experiment_id)
        ON DELETE RESTRICT
);

CREATE TABLE nexus_domain.idempotency_decisions (
    actor_key text NOT NULL,
    operation text NOT NULL,
    idempotency_key text NOT NULL,
    arguments_digest text NOT NULL,
    state text NOT NULL CHECK (state IN ('reserved', 'completed', 'failed', 'rejected')),
    result_reference jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, operation, idempotency_key)
);

CREATE TABLE nexus_domain.artifacts (
    actor_key text NOT NULL,
    artifact_id text NOT NULL,
    content_hash text NOT NULL,
    media_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    storage_key text NOT NULL,
    manifest jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    deleted_at timestamptz,
    PRIMARY KEY (actor_key, artifact_id),
    UNIQUE (actor_key, storage_key)
);

CREATE TABLE nexus_domain.artifact_tombstones (
    actor_key text NOT NULL,
    artifact_id text NOT NULL,
    deleted_at timestamptz NOT NULL,
    reason text NOT NULL,
    prior_content_hash text NOT NULL,
    PRIMARY KEY (actor_key, artifact_id)
);

CREATE TABLE nexus_domain.lineage_nodes (
    actor_key text NOT NULL,
    node_id text NOT NULL,
    node_type text NOT NULL,
    schema_version text NOT NULL,
    properties jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, node_id)
);

CREATE TABLE nexus_domain.lineage_edges (
    actor_key text NOT NULL,
    edge_id text NOT NULL,
    edge_type text NOT NULL,
    source_node_id text NOT NULL,
    target_node_id text NOT NULL,
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, edge_id),
    UNIQUE (actor_key, edge_type, source_node_id, target_node_id),
    FOREIGN KEY (actor_key, source_node_id)
        REFERENCES nexus_domain.lineage_nodes (actor_key, node_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (actor_key, target_node_id)
        REFERENCES nexus_domain.lineage_nodes (actor_key, node_id)
        ON DELETE RESTRICT
);

CREATE TABLE nexus_domain.research_sources (
    actor_key text NOT NULL,
    source_record_id text NOT NULL,
    thread_id text NOT NULL,
    run_id text NOT NULL,
    normalized_locator text NOT NULL,
    content_hash text,
    source_metadata jsonb NOT NULL,
    retrieval_status text NOT NULL,
    retrieved_at timestamptz NOT NULL,
    PRIMARY KEY (actor_key, source_record_id),
    UNIQUE (actor_key, run_id, normalized_locator, content_hash)
);

CREATE TABLE nexus_domain.approval_requests (
    actor_key text NOT NULL,
    approval_request_id text NOT NULL,
    thread_id text NOT NULL,
    run_id text NOT NULL,
    action_digest text NOT NULL,
    tool_name text NOT NULL,
    target_boundary text NOT NULL,
    normalized_arguments jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    requested_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    decision_metadata jsonb,
    PRIMARY KEY (actor_key, approval_request_id),
    UNIQUE (actor_key, action_digest)
);

CREATE TABLE nexus_domain.product_events (
    actor_key text NOT NULL,
    event_id text NOT NULL,
    schema_version integer NOT NULL,
    thread_id text,
    run_id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    event_type text NOT NULL,
    sensitivity text NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, event_id),
    UNIQUE (actor_key, run_id, sequence)
);

CREATE TABLE nexus_domain.transactional_outbox (
    actor_key text NOT NULL,
    outbox_id bigint GENERATED ALWAYS AS IDENTITY,
    event_id text NOT NULL,
    topic text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    PRIMARY KEY (actor_key, outbox_id),
    UNIQUE (actor_key, event_id, topic),
    FOREIGN KEY (actor_key, event_id)
        REFERENCES nexus_domain.product_events (actor_key, event_id)
        ON DELETE RESTRICT
);

CREATE INDEX outbox_unpublished_idx
    ON nexus_domain.transactional_outbox (created_at)
    WHERE published_at IS NULL;
