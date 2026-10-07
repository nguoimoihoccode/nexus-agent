"""Focused tests for PostgreSQL configuration wiring."""

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from psycopg.rows import tuple_row

from source.core.config import Settings, create_model
from source.infrastructure import database


def _test_encryption_key() -> str:
    return ("unit-test-key-material-" * 2)[:32]


class DatabaseConfigTests(unittest.TestCase):
    def test_model_disables_parallel_tool_calls(self) -> None:
        with patch("source.core.config.init_chat_model") as init_chat_model:
            create_model()

        self.assertEqual(
            init_chat_model.call_args.kwargs["model_kwargs"],
            {"parallel_tool_calls": False},
        )

    def test_example_environment_has_no_duplicate_keys(self) -> None:
        example = Path(__file__).parents[1] / ".env.example"
        keys = [
            line.split("=", 1)[0]
            for line in example.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "=" in line
        ]

        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(
            set(keys),
            {
                "AI_TRADER_TOKEN",
                "DEEPSEEK_API_KEY",
                "LANGGRAPH_AES_KEY",
                "NEXUS_BACKEND_DB_PASSWORD",
                "NEXUS_BACKEND_DB_USER",
                "NEXUS_OIDC_AUDIENCE",
                "NEXUS_OIDC_CLIENT_ID",
                "NEXUS_OIDC_CLIENT_SECRET",
                "NEXUS_OIDC_ISSUER",
                "NEXUS_OIDC_JWKS_URI",
                "NEXUS_OIDC_SCOPE",
                "NEXUS_APP_ORIGIN",
                "NEXUS_BROWSER_SESSION_AUTH",
                "NEXUS_BROWSER_SESSION_KEY",
                "NEXUS_QUANT_DATA_DB_PASSWORD",
                "NEXUS_QUANT_DATA_DB_USER",
                "NEXUS_QUANT_DATA_WORKER_TOKEN",
                "NEXUS_QUANT_WORKER_DB_PASSWORD",
                "NEXUS_QUANT_WORKER_DB_USER",
                "NEXUS_QUANT_WORKER_TOKEN",
                "NEXUS_TENANT_HMAC_KEY",
                "POSTGRES_PASSWORD",
                "TAVILY_API_KEY",
            },
        )

    def test_quant_researcher_defaults_leave_room_for_full_workflow(self) -> None:
        settings = Settings(_env_file=None, deepseek_api_key="test-key")

        self.assertEqual(settings.quant_researcher_tool_call_limit, 12)
        self.assertEqual(settings.quant_researcher_model_call_limit, 16)
        self.assertEqual(settings.quant_researcher_experiment_tool_call_limit, 1)
        self.assertEqual(settings.quant_researcher_interpretation_tool_call_limit, 1)

    def test_postgres_url_quotes_connection_parts(self) -> None:
        settings = Settings(
            _env_file=None,
            deepseek_api_key="test-key",
            postgres_user="user/name",
            postgres_password="p@ ss",
            postgres_db_name="nexus db",
        )

        self.assertEqual(
            settings.postgres_url,
            "postgresql://user%2Fname:p%40%20ss@localhost:5433/nexus%20db",
        )

    def test_create_db_pool_uses_core_settings(self) -> None:
        fake_settings = SimpleNamespace(
            postgres_url="postgresql://nexus:secret@localhost:5433/nexus_agent",
            postgres_pool_min_size=1,
            postgres_pool_max_size=3,
        )

        with (
            patch.object(database, "settings", fake_settings),
            patch.object(database, "AsyncConnectionPool") as pool_class,
        ):
            database.create_db_pool()

        pool_class.assert_called_once()
        kwargs = pool_class.call_args.kwargs
        self.assertEqual(kwargs["conninfo"], fake_settings.postgres_url)
        self.assertEqual(kwargs["min_size"], fake_settings.postgres_pool_min_size)
        self.assertEqual(kwargs["max_size"], fake_settings.postgres_pool_max_size)
        self.assertIs(kwargs["kwargs"]["row_factory"], tuple_row)
        self.assertIs(kwargs["check"], database._bind_database_actor)
        self.assertIs(kwargs["reset"], database._reset_database_actor)

    def test_pool_checkout_binds_and_reset_clears_actor(self) -> None:
        actor = "v1-" + "a" * 64
        connection = SimpleNamespace(execute=AsyncMock())

        with patch.object(database, "current_actor_key", return_value=actor):
            asyncio.run(database._bind_database_actor(connection))
            asyncio.run(database._reset_database_actor(connection))

        self.assertEqual(
            connection.execute.await_args_list[0].args,
            ("SELECT set_config('nexus.actor_key', %s, false)", (actor,)),
        )
        self.assertEqual(
            connection.execute.await_args_list[1].args,
            ("RESET nexus.actor_key",),
        )

    def test_production_rejects_default_postgres_password(self) -> None:
        with self.assertRaises(ValidationError) as raised:
            Settings(
                _env_file=None,
                deepseek_api_key="test-key",
                nexus_env="production",
            )

        self.assertIn("POSTGRES_PASSWORD must be set", str(raised.exception))

    def test_production_accepts_non_default_postgres_password(self) -> None:
        settings = Settings(
            _env_file=None,
            deepseek_api_key="test-key",
            nexus_env="production",
            postgres_password="not-the-local-default",
            nexus_oidc_issuer="https://identity.example.com",
            nexus_oidc_audience="nexus-api",
            nexus_tenant_hmac_key="test-tenant-key-that-is-at-least-32-chars",
            nexus_quant_worker_token="test-quant-worker-token-that-is-at-least-32-chars",
            nexus_quant_data_worker_token="test-quant-data-token-that-is-at-least-32-chars",
            langgraph_aes_key=_test_encryption_key(),
        )

        self.assertEqual(settings.nexus_env, "production")

    def test_production_rejects_symmetric_oidc_algorithm(self) -> None:
        with self.assertRaises(ValidationError) as raised:
            Settings(
                _env_file=None,
                deepseek_api_key="test-key",
                nexus_env="production",
                postgres_password="not-the-local-default",
                nexus_oidc_issuer="https://identity.example.com",
                nexus_oidc_audience="nexus-api",
                nexus_oidc_allowed_algorithms=["HS256"],
                nexus_tenant_hmac_key="test-tenant-key-that-is-at-least-32-chars",
                nexus_quant_worker_token="test-quant-worker-token-that-is-at-least-32-chars",
                nexus_quant_data_worker_token="test-quant-data-token-that-is-at-least-32-chars",
                langgraph_aes_key=_test_encryption_key(),
            )

        self.assertIn("only asymmetric", str(raised.exception))

    def test_production_browser_session_requires_confidential_client_and_keys(self) -> None:
        with self.assertRaises(ValidationError) as raised:
            Settings(
                _env_file=None,
                deepseek_api_key="test-key",
                nexus_env="production",
                postgres_password="not-the-local-default",
                nexus_oidc_issuer="https://identity.example.com",
                nexus_oidc_audience="nexus-api",
                nexus_browser_session_auth="required",
                nexus_tenant_hmac_key="test-tenant-key-that-is-at-least-32-chars",
                nexus_quant_worker_token="test-quant-worker-token-that-is-at-least-32-chars",
                nexus_quant_data_worker_token="test-quant-data-token-that-is-at-least-32-chars",
                langgraph_aes_key=_test_encryption_key(),
            )

        self.assertIn("NEXUS_OIDC_CLIENT_ID", str(raised.exception))

    def test_production_accepts_required_browser_session_configuration(self) -> None:
        configured = Settings(
            _env_file=None,
            deepseek_api_key="test-key",
            nexus_env="production",
            postgres_password="not-the-local-default",
            nexus_oidc_issuer="https://identity.example.com",
            nexus_oidc_audience="nexus-api",
            nexus_oidc_client_id="nexus-bff",
            nexus_oidc_client_secret="c" * 32,  # pragma: allowlist secret
            nexus_app_origin="https://nexus.example.com",
            nexus_browser_session_auth="required",
            nexus_browser_session_key="s" * 32,  # pragma: allowlist secret
            nexus_tenant_hmac_key="test-tenant-key-that-is-at-least-32-chars",
            nexus_quant_worker_token="test-quant-worker-token-that-is-at-least-32-chars",
            nexus_quant_data_worker_token="test-quant-data-token-that-is-at-least-32-chars",
            langgraph_aes_key=_test_encryption_key(),
        )

        self.assertEqual(configured.nexus_browser_session_auth, "required")

    def test_production_browser_session_rejects_invalid_origin_and_scope(self) -> None:
        base = {
            "_env_file": None,
            "deepseek_api_key": "test-key",
            "nexus_env": "production",
            "postgres_password": "not-the-local-default",
            "nexus_oidc_issuer": "https://identity.example.com",
            "nexus_oidc_audience": "nexus-api",
            "nexus_oidc_client_id": "nexus-bff",
            "nexus_oidc_client_secret": "c" * 32,  # pragma: allowlist secret
            "nexus_browser_session_auth": "required",
            "nexus_browser_session_key": "s" * 32,  # pragma: allowlist secret
            "nexus_tenant_hmac_key": "test-tenant-key-that-is-at-least-32-chars",
            "nexus_quant_worker_token": "test-quant-worker-token-that-is-at-least-32-chars",
            "nexus_quant_data_worker_token": "test-quant-data-token-that-is-at-least-32-chars",
            "langgraph_aes_key": _test_encryption_key(),
        }
        for origin in (
            "http://nexus.example.com",
            "https://nexus.example.com/app",
            "https://user@nexus.example.com",
            "https://nexus.example.com:invalid",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValidationError):
                Settings(**base, nexus_app_origin=origin)
        with self.assertRaisesRegex(ValidationError, "must include openid"):
            Settings(
                **base,
                nexus_app_origin="https://nexus.example.com",
                nexus_oidc_scope="profile",
            )

    def test_production_rejects_noncanonical_internal_worker_url(self) -> None:
        with self.assertRaises(ValidationError) as raised:
            Settings(
                _env_file=None,
                deepseek_api_key="test-key",
                nexus_env="production",
                postgres_password="not-the-local-default",
                nexus_oidc_issuer="https://identity.example.com",
                nexus_oidc_audience="nexus-api",
                nexus_tenant_hmac_key="test-tenant-key-that-is-at-least-32-chars",
                nexus_quant_worker_token=(
                    "test-quant-worker-token-that-is-at-least-32-chars"
                ),
                nexus_quant_data_worker_token=(
                    "test-quant-data-token-that-is-at-least-32-chars"
                ),
                quant_worker_url="https://attacker.example/collect",
                langgraph_aes_key=_test_encryption_key(),
            )

        self.assertIn("canonical private service", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
