"""PostgreSQL experiment repository checks enabled with a disposable URI."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from tests.test_service import REQUEST, write_manifest_dataset
from worker.models import ExperimentRequest, JobStatus
from worker.security import actor_context
from worker.domain import WorkerError
from worker.service import QlibExperimentWorker

POSTGRES_URI = os.environ.get("NEXUS_TEST_POSTGRES_URI", "").strip()
ADMIN_URI = os.environ.get("NEXUS_TEST_POSTGRES_ADMIN_URI", POSTGRES_URI).strip()


@unittest.skipUnless(POSTGRES_URI, "NEXUS_TEST_POSTGRES_URI is not configured")
class QuantWorkerPostgresIntegrationTests(unittest.TestCase):
    def test_graceful_shutdown_requeues_live_lease_for_another_worker(self):
        actor = "v1-" + uuid4().hex + uuid4().hex
        with tempfile.TemporaryDirectory() as temporary, actor_context(actor):
            root = Path(temporary)
            data_dir = root / "qlib"
            artifacts_dir = root / "artifacts"
            data_dir.mkdir()
            bootstrap = QlibExperimentWorker(data_dir, artifacts_dir)
            dataset_id, dataset_dir = write_manifest_dataset(bootstrap)
            actor_data_dir = data_dir / actor
            actor_data_dir.mkdir()
            scoped_dataset_dir = actor_data_dir / dataset_id
            dataset_dir.rename(scoped_dataset_dir)
            manifest = json.loads(
                (scoped_dataset_dir / "manifest.json").read_text(encoding="utf-8")
            )
            bootstrap.close()
            self._register_dataset(actor, manifest)

            class ShutdownRunner:
                def __init__(self):
                    self.started = threading.Event()

                def run_controllable(self, *_args, cancellation, **_kwargs):
                    self.started.set()
                    cancellation.wait(timeout=2)
                    raise RuntimeError("isolated execution stopped")

            first_runner = ShutdownRunner()
            first = QlibExperimentWorker(
                data_dir,
                artifacts_dir,
                runner=first_runner,
                domain_database_url=POSTGRES_URI,
            )
            self.assertTrue(first.readiness())
            request = ExperimentRequest.model_validate(
                {
                    **REQUEST.model_dump(mode="json"),
                    "dataset_id": dataset_id,
                    "universe": "manifest_demo",
                    "idempotency_key": "graceful-shutdown-integration",
                }
            )
            accepted = first.submit_experiment(request)
            self.assertTrue(first_runner.started.wait(timeout=1))
            first.close(wait=True)

            second = QlibExperimentWorker(
                data_dir,
                artifacts_dir,
                runner=lambda *_args, **_kwargs: {"signal_metrics": {"ic": 0.08}},
                domain_database_url=POSTGRES_URI,
            )
            second.recover_interrupted()
            for _ in range(100):
                recovered = second.load_record(accepted.experiment_id)
                if recovered and recovered.status == JobStatus.COMPLETED:
                    break
                time.sleep(0.01)
            second.close(wait=True)

        self.assertEqual(recovered.status, JobStatus.COMPLETED)
        with psycopg.connect(ADMIN_URI) as connection:
            attempts = connection.execute(
                """
                SELECT status, stage FROM nexus_domain.job_attempts
                WHERE actor_key = %s AND experiment_id = %s
                ORDER BY attempt
                """,
                (actor, accepted.experiment_id),
            ).fetchall()
            requeued = connection.execute(
                """
                SELECT count(*) FROM nexus_domain.product_events
                WHERE actor_key = %s AND run_id = %s
                  AND event_type = 'worker.requeued'
                  AND payload ->> 'stage' = 'shutdown_recovery'
                """,
                (actor, accepted.experiment_id),
            ).fetchone()[0]
        self.assertEqual(attempts, [("interrupted", "shutdown_recovery"), ("completed", "completed")])
        self.assertEqual(requeued, 1)

    def test_global_active_limit_is_durable_across_worker_instances(self):
        actor_one = "v1-" + uuid4().hex + uuid4().hex
        actor_two = "v1-" + uuid4().hex + uuid4().hex
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_dir = root / "qlib"
            artifacts_dir = root / "artifacts"
            data_dir.mkdir()
            with actor_context(actor_one):
                filesystem_worker = QlibExperimentWorker(data_dir, artifacts_dir)
                dataset_id, dataset_dir = write_manifest_dataset(filesystem_worker)
                actor_one_dir = data_dir / actor_one
                actor_one_dir.mkdir()
                scoped_one = actor_one_dir / dataset_id
                dataset_dir.rename(scoped_one)
                manifest = json.loads(
                    (scoped_one / "manifest.json").read_text(encoding="utf-8")
                )
                filesystem_worker.close()
            actor_two_dir = data_dir / actor_two
            actor_two_dir.mkdir()
            shutil.copytree(scoped_one, actor_two_dir / dataset_id)
            self._register_dataset(actor_one, manifest)
            self._register_dataset(actor_two, manifest)
            with psycopg.connect(ADMIN_URI) as connection:
                existing_active = connection.execute(
                    """
                    SELECT count(*) FROM nexus_domain.experiments
                    WHERE status IN ('queued', 'leased', 'running', 'cancelling')
                    """
                ).fetchone()[0]
            global_limit = existing_active + 1
            release = threading.Event()

            def blocking_runner(*_args, **_kwargs):
                release.wait(timeout=2)
                return {"signal_metrics": {"ic": 0.01}}

            worker_one = QlibExperimentWorker(
                data_dir,
                artifacts_dir,
                runner=blocking_runner,
                domain_database_url=POSTGRES_URI,
                global_active_job_limit=global_limit,
            )
            worker_two = QlibExperimentWorker(
                data_dir,
                artifacts_dir,
                runner=blocking_runner,
                domain_database_url=POSTGRES_URI,
                global_active_job_limit=global_limit,
            )
            request = ExperimentRequest.model_validate(
                {
                    **REQUEST.model_dump(mode="json"),
                    "dataset_id": dataset_id,
                    "universe": "manifest_demo",
                    "idempotency_key": "global-limit-one",
                }
            )
            with actor_context(actor_one):
                accepted = worker_one.submit_experiment(request)
                for _ in range(100):
                    running = worker_one.load_record(accepted.experiment_id)
                    if running and running.status == JobStatus.RUNNING:
                        break
                    time.sleep(0.01)
            with actor_context(actor_two), self.assertRaises(WorkerError) as raised:
                worker_two.submit_experiment(
                    request.model_copy(update={"idempotency_key": "global-limit-two"})
                )
            release.set()
            worker_one.close(wait=True)
            worker_two.close(wait=True)

        self.assertEqual(raised.exception.code, "global_experiment_quota_exceeded")

    def test_expired_lease_recovery_is_audited_and_cancelling_becomes_terminal(self):
        actor = "v1-" + uuid4().hex + uuid4().hex
        with tempfile.TemporaryDirectory() as temporary, actor_context(actor):
            root = Path(temporary)
            data_dir = root / "qlib"
            artifacts_dir = root / "artifacts"
            data_dir.mkdir()
            filesystem_worker = QlibExperimentWorker(data_dir, artifacts_dir)
            dataset_id, dataset_dir = write_manifest_dataset(filesystem_worker)
            actor_data_dir = data_dir / actor
            actor_data_dir.mkdir()
            scoped_dataset_dir = actor_data_dir / dataset_id
            dataset_dir.rename(scoped_dataset_dir)
            manifest = json.loads(
                (scoped_dataset_dir / "manifest.json").read_text(encoding="utf-8")
            )
            filesystem_worker.close()
            self._register_dataset(actor, manifest)

            request = ExperimentRequest.model_validate(
                {
                    **REQUEST.model_dump(mode="json"),
                    "dataset_id": dataset_id,
                    "universe": "manifest_demo",
                    "idempotency_key": "recovery-integration",
                }
            )
            recovering_id = "exp_v1_" + uuid4().hex
            cancelling_id = "exp_v1_" + uuid4().hex
            expired_id = "exp_v1_" + uuid4().hex
            self._seed_interrupted_experiment(
                actor,
                recovering_id,
                request,
                status="running",
                cancellation=None,
            )

            class VersionedRunner:
                def __init__(self):
                    self.calls = 0

                @staticmethod
                def runtime_versions(_request):
                    return {"python": "3.11-test", "model": "lightgbm-test"}

                def __call__(self, *_args, **kwargs):
                    if kwargs.get("experiment_id") == recovering_id:
                        self.calls += 1
                    return {"signal_metrics": {"ic": 0.07}}

            runner = VersionedRunner()
            worker = QlibExperimentWorker(
                data_dir,
                artifacts_dir,
                runner=runner,
                domain_database_url=POSTGRES_URI,
            )
            self.assertGreaterEqual(worker.recover_interrupted(), 1)
            for _ in range(100):
                recovered = worker.load_record(recovering_id)
                if recovered and recovered.status == JobStatus.COMPLETED:
                    break
                time.sleep(0.01)

            self._seed_interrupted_experiment(
                actor,
                cancelling_id,
                request.model_copy(update={"idempotency_key": "cancel-recovery"}),
                status="cancelling",
                cancellation={
                    "reason_category": "user_requested",
                    "requested_by_actor": actor,
                    "requested_at": "2026-07-14T00:00:00Z",
                },
            )
            self.assertGreaterEqual(worker.recover_interrupted(), 1)
            cancelled = worker.load_record(cancelling_id)
            self._seed_expired_queued_experiment(
                actor,
                expired_id,
                request.model_copy(update={"idempotency_key": "queued-expiry"}),
            )
            self.assertGreaterEqual(worker.recover_interrupted(), 1)
            expired = worker.load_record(expired_id)
            worker.close(wait=True)

        self.assertEqual(recovered.status, JobStatus.COMPLETED)
        self.assertEqual(runner.calls, 1)
        self.assertEqual(cancelled.status, JobStatus.CANCELLED)
        self.assertEqual(cancelled.cancellation["resulting_state"], "cancelled")
        self.assertIn("completed_at", cancelled.cancellation)
        self.assertEqual(expired.status, JobStatus.EXPIRED)
        self.assertEqual(expired.error.code, "queued_job_expired")
        with psycopg.connect(ADMIN_URI) as connection:
            recovery_attempts = connection.execute(
                """
                SELECT status, runtime_versions
                FROM nexus_domain.job_attempts
                WHERE actor_key = %s AND experiment_id = %s
                ORDER BY attempt
                """,
                (actor, recovering_id),
            ).fetchall()
            cancellation_attempt = connection.execute(
                """
                SELECT status, finished_at
                FROM nexus_domain.job_attempts
                WHERE actor_key = %s AND experiment_id = %s
                """,
                (actor, cancelling_id),
            ).fetchone()
            recovery_events = connection.execute(
                """
                SELECT event_type
                FROM nexus_domain.product_events
                WHERE actor_key = %s AND run_id = %s
                ORDER BY sequence
                """,
                (actor, recovering_id),
            ).fetchall()
        self.assertEqual([row[0] for row in recovery_attempts], ["interrupted", "completed"])
        self.assertEqual(recovery_attempts[1][1]["model"], "lightgbm-test")
        self.assertEqual(cancellation_attempt[0], "cancelled")
        self.assertIsNotNone(cancellation_attempt[1])
        self.assertEqual(recovery_events[0][0], "worker.requeued")

    def test_experiment_state_is_actor_scoped_and_commits_outbox(self):
        actor = "v1-" + uuid4().hex + uuid4().hex
        other_actor = "v1-" + uuid4().hex + uuid4().hex
        with tempfile.TemporaryDirectory() as temporary, actor_context(actor):
            root = Path(temporary)
            data_dir = root / "qlib"
            artifacts_dir = root / "artifacts"
            data_dir.mkdir()
            filesystem_worker = QlibExperimentWorker(data_dir, artifacts_dir)
            dataset_id, dataset_dir = write_manifest_dataset(filesystem_worker)
            actor_data_dir = data_dir / actor
            actor_data_dir.mkdir()
            scoped_dataset_dir = actor_data_dir / dataset_id
            dataset_dir.rename(scoped_dataset_dir)
            dataset_dir = scoped_dataset_dir
            manifest = json.loads(
                (dataset_dir / "manifest.json").read_text(encoding="utf-8")
            )
            filesystem_worker.close()
            self._register_dataset(actor, manifest)

            worker = QlibExperimentWorker(
                data_dir,
                artifacts_dir,
                runner=lambda *_args, **_kwargs: {"signal_metrics": {"ic": 0.03}},
                domain_database_url=POSTGRES_URI,
            )
            request = ExperimentRequest.model_validate(
                {
                    **REQUEST.model_dump(mode="json"),
                    "dataset_id": dataset_id,
                    "universe": "manifest_demo",
                    "idempotency_key": "integration-retry-key",
                }
            )
            accepted = worker.submit_experiment(request)
            for _ in range(100):
                record = worker.load_record(accepted.experiment_id)
                if record and record.status == JobStatus.COMPLETED:
                    break
                time.sleep(0.01)
            repeated = worker.submit_experiment(request)
            conflicting = ExperimentRequest.model_validate(
                {
                    **request.model_dump(mode="json"),
                    "strategy": {
                        **request.strategy.model_dump(mode="json"),
                        "topk": request.strategy.topk + 1,
                    },
                }
            )
            with self.assertRaises(WorkerError) as conflict:
                worker.submit_experiment(conflicting)
            worker.close(wait=True)

            self.assertEqual(record.status, JobStatus.COMPLETED)
            self.assertEqual(record.result["signal_metrics"]["ic"], 0.03)
            self.assertEqual(repeated.experiment_id, accepted.experiment_id)
            self.assertEqual(conflict.exception.code, "idempotency_conflict")
            with actor_context(other_actor):
                isolated = QlibExperimentWorker(
                    data_dir,
                    artifacts_dir,
                    domain_database_url=POSTGRES_URI,
                )
                with self.assertRaises(WorkerError) as raised:
                    isolated.validate_dataset(dataset_id, "manifest_demo")
                isolated.close()
            self.assertEqual(raised.exception.code, "unknown_dataset")
        with psycopg.connect(ADMIN_URI) as connection:
            row = connection.execute(
                """
                SELECT status,
                       (SELECT count(*) FROM nexus_domain.job_attempts a
                        WHERE a.actor_key = e.actor_key
                          AND a.experiment_id = e.experiment_id),
                       (SELECT count(*) FROM nexus_domain.product_events p
                        WHERE p.actor_key = e.actor_key
                          AND p.run_id = e.experiment_id),
                       (SELECT count(*) FROM nexus_domain.transactional_outbox o
                        JOIN nexus_domain.product_events p
                          ON p.actor_key = o.actor_key AND p.event_id = o.event_id
                        WHERE p.actor_key = e.actor_key
                          AND p.run_id = e.experiment_id)
                FROM nexus_domain.experiments e
                WHERE actor_key = %s AND experiment_id = %s
                """,
                (actor, accepted.experiment_id),
            ).fetchone()
            lineage = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM nexus_domain.lineage_nodes
                   WHERE actor_key = %s AND node_id = %s
                     AND node_type = 'experiment'),
                  (SELECT count(*) FROM nexus_domain.lineage_nodes
                   WHERE actor_key = %s AND node_type = 'metric'
                     AND properties ->> 'experiment_id' = %s),
                  (SELECT count(*) FROM nexus_domain.lineage_edges
                   WHERE actor_key = %s AND source_node_id = %s
                     AND edge_type = 'executed_as_attempt'),
                  (SELECT count(*) FROM nexus_domain.lineage_edges
                   WHERE actor_key = %s AND source_node_id = %s
                     AND edge_type = 'produced_metric')
                """,
                (
                    actor,
                    accepted.experiment_id,
                    actor,
                    accepted.experiment_id,
                    actor,
                    accepted.experiment_id,
                    actor,
                    accepted.experiment_id,
                ),
            ).fetchone()
        self.assertEqual(row, ("completed", 1, 4, 4))
        self.assertEqual(lineage, (1, 1, 1, 1))

    def test_expired_artifact_is_tombstoned_audited_and_unreadable(self):
        actor = "v1-" + uuid4().hex + uuid4().hex
        other_actor = "v1-" + uuid4().hex + uuid4().hex
        with tempfile.TemporaryDirectory() as temporary, actor_context(actor):
            root = Path(temporary)
            data_dir = root / "qlib"
            artifacts_dir = root / "artifacts"
            data_dir.mkdir()
            bootstrap = QlibExperimentWorker(data_dir, artifacts_dir)
            dataset_id, dataset_dir = write_manifest_dataset(bootstrap)
            actor_data_dir = data_dir / actor
            actor_data_dir.mkdir()
            scoped_dataset_dir = actor_data_dir / dataset_id
            dataset_dir.rename(scoped_dataset_dir)
            manifest = json.loads(
                (scoped_dataset_dir / "manifest.json").read_text(encoding="utf-8")
            )
            bootstrap.close()
            self._register_dataset(actor, manifest)

            def artifact_runner(*_args, **kwargs):
                kwargs["artifact_dir"].joinpath("predictions.parquet").write_bytes(
                    b"immutable-predictions"
                )
                return {"signal_metrics": {"ic": 0.04}}

            worker = QlibExperimentWorker(
                data_dir,
                artifacts_dir,
                runner=artifact_runner,
                domain_database_url=POSTGRES_URI,
            )
            request = ExperimentRequest.model_validate(
                {
                    **REQUEST.model_dump(mode="json"),
                    "dataset_id": dataset_id,
                    "universe": "manifest_demo",
                    "idempotency_key": "artifact-expiry-integration",
                }
            )
            accepted = worker.submit_experiment(request)
            for _ in range(100):
                record = worker.load_record(accepted.experiment_id)
                if record and record.status == JobStatus.COMPLETED:
                    break
                time.sleep(0.01)
            artifact = record.result["artifacts"][0]
            path, _manifest = worker.artifact_path(artifact["artifact_id"])
            with psycopg.connect(ADMIN_URI) as connection:
                connection.execute(
                    """
                    UPDATE nexus_domain.artifacts
                    SET expires_at = now() - interval '1 second'
                    WHERE actor_key = %s AND artifact_id = %s
                    """,
                    (actor, artifact["artifact_id"]),
                )
            worker.repository.expire_artifacts()
            tombstone = worker.artifact_manifest(artifact["artifact_id"])
            with self.assertRaises(WorkerError) as unavailable:
                worker.artifact_path(artifact["artifact_id"])
            self.assertFalse(path.exists())
            self.assertEqual(tombstone["storage_status"], "deleted")
            self.assertEqual(tombstone["reason"], "retention_expired")
            self.assertEqual(unavailable.exception.code, "artifact_deleted")
            with actor_context(other_actor):
                with self.assertRaises(WorkerError) as hidden:
                    worker.artifact_manifest(artifact["artifact_id"])
            self.assertEqual(hidden.exception.code, "unknown_artifact")
            worker.close(wait=True)

        with psycopg.connect(ADMIN_URI) as connection:
            evidence = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM nexus_domain.artifact_tombstones
                   WHERE actor_key = %s AND artifact_id = %s),
                  (SELECT count(*) FROM nexus_domain.product_events
                   WHERE actor_key = %s AND run_id = %s
                     AND event_type IN ('artifact.expired', 'artifact.deleted'))
                """,
                (
                    actor,
                    artifact["artifact_id"],
                    actor,
                    accepted.experiment_id,
                ),
            ).fetchone()
        self.assertEqual(evidence, (1, 2))

    @staticmethod
    def _register_dataset(actor: str, manifest: dict) -> None:
        request_fingerprint = "rqf_v1_" + uuid4().hex + uuid4().hex
        staging_id = manifest["source_staging_revision_id"]
        with psycopg.connect(ADMIN_URI) as connection:
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO nexus_domain.request_fingerprints
                        (actor_key, request_fingerprint, namespace,
                         schema_version, normalized_request)
                    VALUES (%s, %s, 'integration_seed', '1', %s)
                    """,
                    (actor, request_fingerprint, Jsonb({"seed": True})),
                )
                connection.execute(
                    """
                    INSERT INTO nexus_domain.staging_revisions
                        (actor_key, staging_revision_id, request_fingerprint,
                         content_hash, schema_version, metadata, status)
                    VALUES (%s, %s, %s, %s, '1', %s, 'ready')
                    """,
                    (
                        actor,
                        staging_id,
                        request_fingerprint,
                        "sha256:" + uuid4().hex + uuid4().hex,
                        Jsonb({"seed": True}),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO nexus_domain.dataset_revisions
                        (actor_key, dataset_revision_id,
                         source_staging_revision_id, manifest_hash,
                         schema_version, status, limitations, ready_at)
                    VALUES (%s, %s, %s, %s, '1', 'ready', %s, now())
                    """,
                    (
                        actor,
                        manifest["dataset_revision_id"],
                        staging_id,
                        manifest["manifest_hash"],
                        Jsonb({}),
                    ),
                )

    @staticmethod
    def _seed_interrupted_experiment(
        actor: str,
        experiment_id: str,
        request: ExperimentRequest,
        *,
        status: str,
        cancellation: dict | None,
    ) -> None:
        with psycopg.connect(ADMIN_URI) as connection, connection.transaction():
            connection.execute(
                """
                INSERT INTO nexus_domain.experiments
                    (actor_key, experiment_id, dataset_revision_id,
                     specification_digest, specification, status,
                     lease_owner, lease_expires_at, heartbeat_at, cancellation,
                     idempotency_key, arguments_digest, attempt_limit)
                VALUES (%s, %s, %s, %s, %s, %s, 'dead-worker',
                        now() - interval '1 minute', now() - interval '2 minutes',
                        %s, %s, %s, 2)
                """,
                (
                    actor,
                    experiment_id,
                    request.dataset_id,
                    "sha256:" + uuid4().hex + uuid4().hex,
                    Jsonb(request.model_dump(mode="json")),
                    status,
                    Jsonb(cancellation) if cancellation is not None else None,
                    request.idempotency_key,
                    "act_v1_" + uuid4().hex + uuid4().hex,
                ),
            )
            connection.execute(
                """
                INSERT INTO nexus_domain.job_attempts
                    (actor_key, experiment_id, attempt, lease_owner,
                     started_at, status, stage)
                VALUES (%s, %s, 1, 'dead-worker',
                        now() - interval '2 minutes', %s, 'execution')
                """,
                (actor, experiment_id, status),
            )

    @staticmethod
    def _seed_expired_queued_experiment(
        actor: str,
        experiment_id: str,
        request: ExperimentRequest,
    ) -> None:
        with psycopg.connect(ADMIN_URI) as connection, connection.transaction():
            connection.execute(
                """
                INSERT INTO nexus_domain.experiments
                    (actor_key, experiment_id, dataset_revision_id,
                     specification_digest, specification, status,
                     idempotency_key, arguments_digest, attempt_limit,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s, 2,
                        now() - interval '2 days', now() - interval '2 days')
                """,
                (
                    actor,
                    experiment_id,
                    request.dataset_id,
                    "sha256:" + uuid4().hex + uuid4().hex,
                    Jsonb(request.model_dump(mode="json")),
                    request.idempotency_key,
                    "act_v1_" + uuid4().hex + uuid4().hex,
                ),
            )


if __name__ == "__main__":
    unittest.main()
