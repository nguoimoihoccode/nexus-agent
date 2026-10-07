DROP TRIGGER IF EXISTS protect_referenced_dataset_revision
    ON nexus_domain.dataset_revisions;
DROP FUNCTION IF EXISTS nexus_domain.protect_referenced_dataset_revision();
DROP TRIGGER IF EXISTS protect_referenced_dataset_manifest_file
    ON nexus_domain.dataset_manifest_files;
DROP FUNCTION IF EXISTS nexus_domain.protect_referenced_dataset_manifest_file();

CREATE FUNCTION nexus_domain.protect_referenced_dataset_revision()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, nexus_domain AS $$
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
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, nexus_domain AS $$
DECLARE
    target_actor text;
    target_dataset_revision text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_actor := OLD.actor_key;
        target_dataset_revision := OLD.dataset_revision_id;
    ELSE
        target_actor := NEW.actor_key;
        target_dataset_revision := NEW.dataset_revision_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM nexus_domain.experiments
        WHERE actor_key = target_actor
          AND dataset_revision_id = target_dataset_revision
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
BEFORE INSERT OR UPDATE OR DELETE ON nexus_domain.dataset_manifest_files
FOR EACH ROW EXECUTE FUNCTION nexus_domain.protect_referenced_dataset_manifest_file();
