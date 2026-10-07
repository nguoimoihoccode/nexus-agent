CREATE INDEX experiments_actor_created_idx
    ON nexus_domain.experiments
        (actor_key, created_at DESC, experiment_id DESC);
