"""Repository-level security assertions for the private-beta baseline."""

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class SecurityInventoryTests(unittest.TestCase):
    def test_beta_profile_keeps_identity_worker_and_database_boundaries(self) -> None:
        compose = (REPO_ROOT / "docker-compose.beta.yml").read_text(encoding="utf-8")

        for required in (
            "NEXUS_OIDC_ISSUER",
            "NEXUS_OIDC_AUDIENCE",
            "NEXUS_TENANT_HMAC_KEY",
            "NEXUS_QUANT_WORKER_TOKEN",
            "NEXUS_QUANT_DATA_WORKER_TOKEN",
            "LANGGRAPH_AES_KEY",
            "NEXUS_BACKEND_DB_USER",
            "NEXUS_QUANT_DATA_DB_USER",
            "NEXUS_QUANT_WORKER_DB_USER",
            "no-new-privileges:true",
            "read_only: true",
            "pids_limit:",
            "internal: true",
        ):
            self.assertIn(required, compose)
        self.assertGreaterEqual(compose.count("/ready"), 3)

    def test_base_profile_uses_pinned_official_postgres_and_named_worker_volumes(self) -> None:
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertRegex(
            compose,
            r"image: postgres:16\.14-alpine3\.24@sha256:[0-9a-f]{64}",
        )
        self.assertNotIn("infra/postgres", compose)
        self.assertIn("quant-artifacts:/data/artifacts", compose)
        self.assertIn("qlib-data:/data/qlib", compose)
        self.assertIn(
            '"${APP_BIND_HOST:-127.0.0.1}:${APP_PORT:-8080}:8080"', compose
        )

    def test_actor_rls_uses_expand_contract_migrations(self) -> None:
        expand = (
            REPO_ROOT / "backend/migrations/domain/0009_actor_rls_expand.up.sql"
        ).read_text(encoding="utf-8")
        contract = (
            REPO_ROOT / "backend/migrations/domain/0010_actor_rls_contract.up.sql"
        ).read_text(encoding="utf-8")
        memory_items = (
            REPO_ROOT / "backend/migrations/domain/0011_user_memory_items.up.sql"
        ).read_text(encoding="utf-8")
        authorization_leases = (
            REPO_ROOT / "backend/migrations/domain/0012_authorization_leases.up.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("nexus.actor_key", expand)
        self.assertIn("CREATE POLICY nexus_actor_isolation", expand)
        self.assertIn("SECURITY DEFINER", expand)
        self.assertIn("session_user", expand)
        self.assertNotIn("BYPASSRLS", expand)
        self.assertNotIn("system:", expand)
        self.assertIn("ENABLE ROW LEVEL SECURITY", contract)
        self.assertIn("CREATE POLICY nexus_actor_isolation", memory_items)
        self.assertIn("ENABLE ROW LEVEL SECURITY", memory_items)
        self.assertIn("CREATE POLICY nexus_actor_isolation", authorization_leases)
        self.assertIn("ENABLE ROW LEVEL SECURITY", authorization_leases)
        self.assertIn("authorization_lease_sensitive_mode", authorization_leases)

    def test_runtime_keeps_strict_serialization_and_auth_first_http(self) -> None:
        dockerfile = (REPO_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
        config = json.loads(
            (REPO_ROOT / "backend/langgraph.json").read_text(encoding="utf-8")
        )

        self.assertIn("LANGGRAPH_STRICT_MSGPACK='true'", dockerfile)
        self.assertFalse(config["checkpointer"]["serde"]["pickle_fallback"])
        self.assertEqual(config["http"]["middleware_order"], "auth_first")
        self.assertTrue(config["http"]["disable_mcp"])
        self.assertTrue(config["http"]["disable_a2a"])
        self.assertTrue(config["http"]["disable_webhooks"])

    def test_supervisor_prompt_and_memory_are_not_repo_file_backed(self) -> None:
        factory = (REPO_ROOT / "backend/source/agents/factory.py").read_text(
            encoding="utf-8"
        )
        auth = (REPO_ROOT / "backend/source/security/auth.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("SUPERVISOR_SYSTEM_PROMPT", factory)
        self.assertIn("save_user_memory", factory)
        self.assertNotIn("auth.on." + "store", auth)

    def test_removed_infrastructure_dependencies_do_not_return(self) -> None:
        backend_project = (REPO_ROOT / "backend/pyproject.toml").read_text(
            encoding="utf-8"
        )
        worker_project = (REPO_ROOT / "quant-worker/pyproject.toml").read_text(
            encoding="utf-8"
        )

        self.assertIn('"psycopg[binary,pool]', backend_project)
        self.assertIn('"fastapi', worker_project)
        self.assertNotIn('"redis', backend_project)
        self.assertNotIn('"boto3', worker_project)


if __name__ == "__main__":
    unittest.main()
