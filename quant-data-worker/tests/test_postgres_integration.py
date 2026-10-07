"""PostgreSQL adapter checks enabled with NEXUS_TEST_POSTGRES_URI."""

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import psycopg

from worker.adapters import MarketDataStore, PostgresQuantDataControlPlane
from worker.models import BuildQlibDatasetRequest, FetchOhlcvRequest
from worker.security import actor_context
from worker.domain import WorkerError
from worker.service import OpenBBDataWorker
from tests.test_service import FakeFetcher, sample_frame

POSTGRES_URI = os.environ.get("NEXUS_TEST_POSTGRES_URI", "").strip()
ADMIN_URI = os.environ.get("NEXUS_TEST_POSTGRES_ADMIN_URI", POSTGRES_URI).strip()


@unittest.skipUnless(POSTGRES_URI, "NEXUS_TEST_POSTGRES_URI is not configured")
class QuantDataPostgresIntegrationTests(unittest.TestCase):
    def test_staging_dataset_event_and_outbox_commit_for_one_actor(self):
        actor = "v1-" + uuid4().hex + uuid4().hex
        with tempfile.TemporaryDirectory() as temporary, actor_context(actor):
            root = Path(temporary)
            (root / "staging").mkdir()
            (root / "qlib").mkdir()
            calls = 0

            class CountingFetcher(FakeFetcher):
                def fetch_ohlcv(self, request):
                    nonlocal calls
                    calls += 1
                    return super().fetch_ohlcv(request)

            control_plane = PostgresQuantDataControlPlane(POSTGRES_URI)
            worker = OpenBBDataWorker(
                MarketDataStore(root / "staging", root / "qlib"),
                CountingFetcher(sample_frame()),
                control_plane,
            )
            self.assertTrue(worker.readiness())
            request = FetchOhlcvRequest.model_validate(
                {
                    "symbols": ["AAPL", "MSFT"],
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-05",
                    "provider": "yfinance",
                    "idempotency_key": "postgres-provider-retry",
                }
            )
            staged = worker.fetch_openbb_ohlcv(request)
            worker.close()

            restarted = OpenBBDataWorker(
                MarketDataStore(root / "staging", root / "qlib"),
                CountingFetcher(sample_frame()),
                PostgresQuantDataControlPlane(POSTGRES_URI),
            )
            repeated = restarted.fetch_openbb_ohlcv(request)
            conflicting = request.model_copy(update={"symbols": ["AAPL"]})
            with self.assertRaises(WorkerError) as raised:
                restarted.fetch_openbb_ohlcv(conflicting)
            dataset = restarted.build_qlib_dataset(
                BuildQlibDatasetRequest.model_validate(
                    {
                        "dataset_staging_id": staged.staging_revision_id,
                        "dataset_alias": "integration-dataset",
                        "universe_name": "custom_us_2",
                    }
                )
            )
            restarted.close()

        self.assertEqual(repeated.staging_revision_id, staged.staging_revision_id)
        self.assertEqual(calls, 1)
        self.assertEqual(raised.exception.code, "idempotency_conflict")

        with psycopg.connect(ADMIN_URI) as connection:
            staging = connection.execute(
                "SELECT count(*) FROM nexus_domain.staging_revisions "
                "WHERE actor_key = %s",
                (actor,),
            ).fetchone()[0]
            revisions = connection.execute(
                "SELECT count(*) FROM nexus_domain.dataset_revisions "
                "WHERE actor_key = %s AND dataset_revision_id = %s",
                (actor, dataset.dataset_revision_id),
            ).fetchone()[0]
            events = connection.execute(
                "SELECT count(*) FROM nexus_domain.product_events "
                "WHERE actor_key = %s",
                (actor,),
            ).fetchone()[0]
            outbox = connection.execute(
                "SELECT count(*) FROM nexus_domain.transactional_outbox "
                "WHERE actor_key = %s",
                (actor,),
            ).fetchone()[0]
            limitations = connection.execute(
                """
                SELECT limitations FROM nexus_domain.dataset_revisions
                WHERE actor_key = %s AND dataset_revision_id = %s
                """,
                (actor, dataset.dataset_revision_id),
            ).fetchone()[0]
            nodes = connection.execute(
                """
                SELECT node_type FROM nexus_domain.lineage_nodes
                WHERE actor_key = %s
                  AND node_id IN (%s, %s, %s)
                """,
                (
                    actor,
                    staged.request_fingerprint,
                    staged.staging_revision_id,
                    dataset.dataset_revision_id,
                ),
            ).fetchall()
            validation_nodes = connection.execute(
                """
                SELECT count(*) FROM nexus_domain.lineage_nodes
                WHERE actor_key = %s AND node_type = 'validation_result'
                  AND properties ->> 'entity_id' = %s
                """,
                (actor, staged.staging_revision_id),
            ).fetchone()[0]
            edges = connection.execute(
                """
                SELECT edge_type FROM nexus_domain.lineage_edges
                WHERE actor_key = %s
                  AND edge_type IN ('produced_revision', 'transformed_into')
                """,
                (actor,),
            ).fetchall()
        self.assertEqual(staging, 1)
        self.assertEqual(revisions, 1)
        self.assertEqual(events, 2)
        self.assertEqual(outbox, 2)
        self.assertEqual(limitations["production_eligibility"], "research_only")
        self.assertEqual(
            {row[0] for row in nodes},
            {"provider_request", "staging_revision", "dataset_revision"},
        )
        self.assertEqual(
            {row[0] for row in edges},
            {"produced_revision", "transformed_into"},
        )
        self.assertEqual(validation_nodes, 1)


if __name__ == "__main__":
    unittest.main()
