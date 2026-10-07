ALTER TABLE nexus_domain.workflows
    ADD COLUMN thread_id text,
    ADD COLUMN run_id text;

CREATE INDEX workflows_run_idx
    ON nexus_domain.workflows (actor_key, run_id, created_at)
    WHERE run_id IS NOT NULL;

CREATE TABLE nexus_domain.interpretation_reports (
    actor_key text NOT NULL,
    interpretation_report_id text NOT NULL,
    experiment_id text NOT NULL,
    thread_id text,
    run_id text,
    schema_version text NOT NULL,
    idempotency_key text NOT NULL,
    content_digest text NOT NULL,
    structured_content jsonb NOT NULL,
    evidence_references jsonb NOT NULL DEFAULT '[]'::jsonb,
    mapping_origin text NOT NULL CHECK (mapping_origin IN ('model_generated', 'human')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, interpretation_report_id),
    UNIQUE (actor_key, idempotency_key),
    FOREIGN KEY (actor_key, experiment_id)
        REFERENCES nexus_domain.experiments (actor_key, experiment_id)
        ON DELETE RESTRICT
);

CREATE INDEX interpretation_reports_experiment_idx
    ON nexus_domain.interpretation_reports (actor_key, experiment_id, created_at);
CREATE INDEX interpretation_reports_evidence_idx
    ON nexus_domain.interpretation_reports USING gin (evidence_references);

CREATE TABLE nexus_domain.dossier_exports (
    actor_key text NOT NULL,
    export_id text NOT NULL,
    experiment_id text NOT NULL,
    dossier_schema_version text NOT NULL,
    included_evidence_ids jsonb NOT NULL,
    content_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_key, export_id),
    FOREIGN KEY (actor_key, experiment_id)
        REFERENCES nexus_domain.experiments (actor_key, experiment_id)
        ON DELETE RESTRICT
);

CREATE INDEX dossier_exports_experiment_idx
    ON nexus_domain.dossier_exports (actor_key, experiment_id, created_at);

CREATE FUNCTION nexus_domain.protect_referenced_dataset_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM nexus_domain.experiments
        WHERE actor_key = OLD.actor_key
          AND dataset_revision_id = OLD.dataset_revision_id
    ) AND (
        NEW.source_staging_revision_id IS DISTINCT FROM OLD.source_staging_revision_id
        OR NEW.manifest_hash IS DISTINCT FROM OLD.manifest_hash
        OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
        OR NEW.limitations IS DISTINCT FROM OLD.limitations
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.ready_at IS DISTINCT FROM OLD.ready_at
    ) THEN
        RAISE EXCEPTION 'referenced dataset revision evidence is immutable';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER protect_referenced_dataset_revision
BEFORE UPDATE ON nexus_domain.dataset_revisions
FOR EACH ROW EXECUTE FUNCTION nexus_domain.protect_referenced_dataset_revision();

CREATE FUNCTION nexus_domain.protect_referenced_dataset_manifest_file()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM nexus_domain.experiments
        WHERE actor_key = OLD.actor_key
          AND dataset_revision_id = OLD.dataset_revision_id
    ) THEN
        RAISE EXCEPTION 'referenced dataset manifest evidence is immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER protect_referenced_dataset_manifest_file
BEFORE UPDATE OR DELETE ON nexus_domain.dataset_manifest_files
FOR EACH ROW EXECUTE FUNCTION nexus_domain.protect_referenced_dataset_manifest_file();
