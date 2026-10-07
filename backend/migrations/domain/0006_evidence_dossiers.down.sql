DROP TRIGGER IF EXISTS protect_referenced_dataset_manifest_file
    ON nexus_domain.dataset_manifest_files;
DROP FUNCTION IF EXISTS nexus_domain.protect_referenced_dataset_manifest_file();
DROP TRIGGER IF EXISTS protect_referenced_dataset_revision
    ON nexus_domain.dataset_revisions;
DROP FUNCTION IF EXISTS nexus_domain.protect_referenced_dataset_revision();
DROP TABLE IF EXISTS nexus_domain.dossier_exports;
DROP TABLE IF EXISTS nexus_domain.interpretation_reports;
DROP INDEX IF EXISTS nexus_domain.workflows_run_idx;
ALTER TABLE nexus_domain.workflows
    DROP COLUMN IF EXISTS run_id,
    DROP COLUMN IF EXISTS thread_id;
