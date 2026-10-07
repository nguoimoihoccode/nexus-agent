CREATE TABLE nexus_domain.workflows (
    actor_key text NOT NULL,
    workflow_id text NOT NULL,
    workflow_type text NOT NULL CHECK (
        workflow_type IN ('prepare_qlib_dataset', 'run_qlib_experiment')
    ),
    idempotency_key text NOT NULL,
    intent_digest text NOT NULL,
    schema_version text NOT NULL,
    state text NOT NULL,
    stage text NOT NULL,
    normalized_input jsonb NOT NULL,
    output jsonb NOT NULL DEFAULT '{}'::jsonb,
    error jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, workflow_id),
    UNIQUE (actor_key, workflow_type, idempotency_key)
);

CREATE INDEX workflows_resume_idx
    ON nexus_domain.workflows (actor_key, state, updated_at);

CREATE TABLE nexus_domain.workflow_transitions (
    actor_key text NOT NULL,
    workflow_id text NOT NULL,
    sequence integer NOT NULL CHECK (sequence > 0),
    from_state text,
    to_state text NOT NULL,
    stage text NOT NULL,
    event_id text NOT NULL,
    safe_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, workflow_id, sequence),
    UNIQUE (actor_key, event_id),
    FOREIGN KEY (actor_key, workflow_id)
        REFERENCES nexus_domain.workflows (actor_key, workflow_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (actor_key, event_id)
        REFERENCES nexus_domain.product_events (actor_key, event_id)
        ON DELETE RESTRICT
);

CREATE TABLE nexus_domain.replay_events (
    actor_key text NOT NULL,
    run_id text NOT NULL,
    sequence bigint NOT NULL,
    event_id text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (actor_key, run_id, sequence),
    UNIQUE (actor_key, event_id),
    FOREIGN KEY (actor_key, event_id)
        REFERENCES nexus_domain.product_events (actor_key, event_id)
        ON DELETE CASCADE
);

CREATE INDEX replay_events_expiry_idx
    ON nexus_domain.replay_events (expires_at);

CREATE TABLE nexus_domain.publication_effects (
    actor_key text NOT NULL,
    publication_id text NOT NULL,
    publication_type text NOT NULL,
    approval_request_id text NOT NULL,
    action_digest text NOT NULL,
    idempotency_key text NOT NULL,
    target_reference jsonb,
    status text NOT NULL CHECK (status IN ('reserved', 'published', 'failed', 'rejected')),
    safe_error jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, publication_id),
    UNIQUE (actor_key, idempotency_key),
    UNIQUE (actor_key, action_digest),
    FOREIGN KEY (actor_key, approval_request_id)
        REFERENCES nexus_domain.approval_requests
            (actor_key, approval_request_id)
        ON DELETE RESTRICT
);
