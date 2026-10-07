import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from worker import app
from worker.models import DatasetInfo
from worker.security import actor_context


WORKER_TOKEN = "quant-worker-test-token-that-is-32-chars"


class QuantWorkerAppPathTests(unittest.TestCase):
    def test_app_factory_uses_injected_service(self):
        worker = SimpleNamespace(
            list_datasets=lambda: [SimpleNamespace(ready=True)],
        )

        response = TestClient(app.create_app(worker)).get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "worker": "quant-worker", "process_alive": True},
        )

    def test_readiness_uses_dependency_probe_without_requiring_a_dataset(self):
        ready_worker = SimpleNamespace(readiness=lambda: True)
        unavailable_worker = Mock()
        unavailable_worker.readiness.side_effect = RuntimeError("database unavailable")

        ready_response = TestClient(app.create_app(ready_worker)).get("/ready")
        unavailable_response = TestClient(app.create_app(unavailable_worker)).get(
            "/ready"
        )

        self.assertEqual(ready_response.status_code, 200)
        self.assertEqual(ready_response.json()["dependencies_ready"], True)
        self.assertEqual(unavailable_response.status_code, 503)
        self.assertEqual(unavailable_response.json()["dependencies_ready"], False)

    def test_health_returns_provided_request_id(self):
        response = TestClient(app.app).get(
            "/health",
            headers={"X-Request-ID": "req-test-1"},
        )

        self.assertEqual(response.headers["X-Request-ID"], "req-test-1")

    def test_production_api_requires_internal_token_and_actor(self):
        actor = "v1-" + "a" * 64
        with patch.dict(
            "os.environ",
            {
                "NEXUS_ENV": "production",
                "NEXUS_QUANT_WORKER_TOKEN": WORKER_TOKEN,
            },
            clear=False,
        ):
            client = TestClient(
                app.create_app(
                    SimpleNamespace(
                        list_datasets=lambda: [
                            DatasetInfo(
                                dataset_id="dsr_v1_test",
                                path="/tmp/dsr_v1_test",
                                universe="custom_us_1",
                                ready=True,
                            )
                        ],
                    )
                )
            )
            self.assertEqual(client.get("/v1/datasets").status_code, 401)
            self.assertEqual(
                client.get(
                    "/v1/datasets",
                    headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
                ).status_code,
                422,
            )
            self.assertEqual(
                client.get(
                    "/v1/datasets",
                    headers={
                        "Authorization": f"Bearer {WORKER_TOKEN}",
                        "X-Nexus-Actor-Key": actor,
                    },
                ).status_code,
                200,
            )

    def test_production_rejects_other_service_token(self):
        with patch.dict(
            "os.environ",
            {
                "NEXUS_ENV": "production",
                "NEXUS_QUANT_WORKER_TOKEN": WORKER_TOKEN,
                "NEXUS_QUANT_DATA_WORKER_TOKEN": (
                    "quant-data-test-token-that-is-32-chars"
                ),
            },
            clear=False,
        ):
            response = TestClient(
                app.create_app(SimpleNamespace(list_datasets=lambda: []))
            ).get(
                "/v1/datasets",
                headers={
                    "Authorization": (
                        "Bearer quant-data-test-token-that-is-32-chars"
                    ),
                    "X-Nexus-Actor-Key": "v1-" + "a" * 64,
                },
            )

        self.assertEqual(response.status_code, 401)

    def test_actor_context_rejects_invalid_maintenance_actor(self):
        with self.assertRaisesRegex(ValueError, "Invalid actor key"):
            with actor_context("../../other-tenant"):
                pass

    def test_defaults_use_repo_root_data_workspace(self):
        data_dir, artifacts_dir = app.runtime_paths({})
        root = Path(__file__).resolve().parents[2]

        self.assertEqual(data_dir, root / "data" / "qlib")
        self.assertEqual(artifacts_dir, root / "data" / "artifacts")

    def test_specific_env_vars_override_nexus_data_dir(self):
        data_dir, artifacts_dir = app.runtime_paths(
            {
                "NEXUS_DATA_DIR": "/tmp/nexus-data",
                "QLIB_DATA_DIR": "/mnt/qlib",
                "QUANT_ARTIFACTS_DIR": "/mnt/artifacts",
            }
        )

        self.assertEqual(data_dir, Path("/mnt/qlib"))
        self.assertEqual(artifacts_dir, Path("/mnt/artifacts"))

    def test_positive_integer_setting_rejects_invalid_limits(self):
        self.assertEqual(
            app.positive_int_setting({}, "QUANT_WORKER_GLOBAL_ACTIVE_JOB_LIMIT", 4),
            4,
        )
        for raw in ("0", "-1", "not-a-number"):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                app.positive_int_setting(
                    {"QUANT_WORKER_GLOBAL_ACTIVE_JOB_LIMIT": raw},
                    "QUANT_WORKER_GLOBAL_ACTIVE_JOB_LIMIT",
                    4,
                )


if __name__ == "__main__":
    unittest.main()
