import unittest
import json
import logging
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from worker import app
from worker.observability import JsonLogFormatter
from worker import security


class QuantDataWorkerAppPathTests(unittest.TestCase):
    def test_production_uses_only_quant_data_service_token(self):
        actor = "v1-" + "a" * 64
        expected = "quant-data-worker-test-token-at-least-32-chars"
        with patch.dict(
            "os.environ",
            {
                "NEXUS_ENV": "production",
                "NEXUS_QUANT_DATA_WORKER_TOKEN": expected,
                "NEXUS_QUANT_WORKER_TOKEN": (
                    "quant-worker-test-token-at-least-32-chars"
                ),
            },
            clear=False,
        ):
            client = TestClient(app.create_app(Mock()))
            wrong = client.post(
                "/v1/openbb/ohlcv",
                headers={
                    "Authorization": (
                        "Bearer quant-worker-test-token-at-least-32-chars"
                    ),
                    "X-Nexus-Actor-Key": actor,
                },
                json={},
            )
            accepted_by_auth = client.post(
                "/v1/openbb/ohlcv",
                headers={
                    "Authorization": f"Bearer {expected}",
                    "X-Nexus-Actor-Key": actor,
                },
                json={},
            )

        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(accepted_by_auth.status_code, 422)

    def test_app_factory_accepts_injected_service(self):
        worker = Mock()

        response = TestClient(app.create_app(worker)).get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "worker": "quant-data-worker",
                "process_alive": True,
            },
        )
        worker.assert_not_called()

    def test_readiness_uses_dependency_probe(self):
        ready = Mock()
        ready.readiness.return_value = True
        unavailable = Mock()
        unavailable.readiness.side_effect = RuntimeError("database unavailable")

        ready_response = TestClient(app.create_app(ready)).get("/ready")
        unavailable_response = TestClient(app.create_app(unavailable)).get("/ready")

        self.assertEqual(ready_response.status_code, 200)
        self.assertEqual(ready_response.json()["dependencies_ready"], True)
        self.assertEqual(unavailable_response.status_code, 503)
        self.assertEqual(unavailable_response.json()["dependencies_ready"], False)

    def test_actor_directory_is_tenant_scoped(self):
        actor = "v1-" + "b" * 64
        token = security._actor.set(actor)
        try:
            self.assertEqual(
                security.actor_directory(Path("/data/qlib")),
                Path("/data/qlib") / actor,
            )
        finally:
            security._actor.reset(token)

    def test_json_log_formatter_adds_service_context(self):
        formatter = JsonLogFormatter(
            service="quant-data-worker",
            environment="test",
        )
        record = logging.LogRecord(
            "worker.test",
            logging.INFO,
            __file__,
            1,
            "dataset.built",
            (),
            None,
        )

        payload = json.loads(formatter.format(record))

        self.assertEqual(payload["service"], "quant-data-worker")
        self.assertEqual(payload["environment"], "test")
        self.assertEqual(payload["event"], "dataset.built")

    def test_defaults_use_repo_root_data_workspace(self):
        data_dir, staging_dir = app.runtime_paths({})
        root = Path(__file__).resolve().parents[2]

        self.assertEqual(data_dir, root / "data" / "qlib")
        self.assertEqual(staging_dir, root / "data" / "staging")

    def test_specific_env_vars_override_nexus_data_dir(self):
        data_dir, staging_dir = app.runtime_paths(
            {
                "NEXUS_DATA_DIR": "/tmp/nexus-data",
                "QUANT_DATA_WORKER_DATA_DIR": "/mnt/qlib",
                "QUANT_DATA_WORKER_STAGING_DIR": "/mnt/staging",
            }
        )

        self.assertEqual(data_dir, Path("/mnt/qlib"))
        self.assertEqual(staging_dir, Path("/mnt/staging"))


if __name__ == "__main__":
    unittest.main()
