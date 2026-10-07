"""Focused tests for identity, RBAC, and tenant scoping."""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from source.security import auth as auth_module
from source.security.context import actor_key_for_subject, permissions_for_roles


class AuthSecurityTests(unittest.TestCase):
    def test_role_permissions_are_least_privilege(self) -> None:
        quant_scopes = {
            "quant:read",
            "quant:data:write",
            "quant:experiment:run",
            "quant:interpretation:write",
        }
        self.assertTrue(quant_scopes.isdisjoint(permissions_for_roles(["user"])))
        self.assertTrue(quant_scopes <= permissions_for_roles(["analyst"]))
        self.assertNotIn("ai_trader:publish", permissions_for_roles(["analyst"]))
        self.assertIn("ai_trader:publish", permissions_for_roles(["publisher"]))
        self.assertIn("studio:access", permissions_for_roles(["admin"]))

    def test_actor_key_is_stable_and_does_not_expose_subject(self) -> None:
        first = actor_key_for_subject("https://issuer.example", "private-user-id")
        second = actor_key_for_subject("https://issuer.example", "private-user-id")

        self.assertEqual(first, second)
        self.assertRegex(first, r"^v1-[0-9a-f]{64}$")
        self.assertNotIn("private-user-id", first)

    def test_production_auth_maps_oidc_role_to_pseudonymous_identity(self) -> None:
        fake_settings = SimpleNamespace(
            nexus_env="production",
            nexus_oidc_issuer="https://issuer.example",
            nexus_oidc_roles_claim="roles",
        )
        claims = {"sub": "subject-1", "roles": ["analyst"]}
        with (
            patch.object(auth_module, "settings", fake_settings),
            patch.object(auth_module, "_verify_token", AsyncMock(return_value=claims)),
        ):
            user = asyncio.run(
                auth_module.authenticate("Bearer signed-token", "/threads", "POST")
            )

        self.assertIn("quant:experiment:run", user["permissions"])
        self.assertNotEqual(user["identity"], "subject-1")

    def test_verify_token_enforces_audience(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": "subject-1",
                "iss": "https://issuer.example",
                "aud": "wrong-api",
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            private_key,
            algorithm="RS256",
        )
        fake_settings = SimpleNamespace(
            nexus_oidc_allowed_algorithms=["RS256"],
            nexus_oidc_audience="nexus-api",
            nexus_oidc_issuer="https://issuer.example",
        )
        jwk_client = SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(
                key=private_key.public_key()
            )
        )
        with (
            patch.object(auth_module, "settings", fake_settings),
            patch.object(
                auth_module,
                "_discover_jwks_uri",
                AsyncMock(return_value="https://issuer.example/jwks"),
            ),
            patch.object(auth_module, "_jwk_client", return_value=jwk_client),
        ):
            with self.assertRaises(auth_module.Auth.exceptions.HTTPException) as raised:
                asyncio.run(auth_module._verify_token(token))

        self.assertEqual(raised.exception.status_code, 401)

    def test_thread_scope_overwrites_client_owner_metadata(self) -> None:
        ctx = SimpleNamespace(user=SimpleNamespace(identity="v1-" + "a" * 64))
        value = {"metadata": {"owner_key": "attacker", "safe": True}}

        owner_filter = asyncio.run(auth_module.create_thread(ctx, value))

        self.assertEqual(value["metadata"]["owner_key"], ctx.user.identity)
        self.assertEqual(owner_filter, {"owner_key": ctx.user.identity})
        self.assertEqual(value["ttl"]["ttl"], 43_200)

    def test_langgraph_store_has_no_memory_authorization_handler(self) -> None:
        self.assertFalse(hasattr(auth_module, "access_memory"))
        self.assertFalse(hasattr(auth_module, "_scope_memory"))

    def test_run_authorization_restricts_standalone_graphs_to_studio_users(self) -> None:
        value = {
            "assistant_id": auth_module.assistant_id_for_graph("researcher"),
            "kwargs": {},
        }
        regular = SimpleNamespace(
            permissions={"chat:run"},
            user=SimpleNamespace(identity="actor"),
        )
        studio = SimpleNamespace(
            permissions={"chat:run", "studio:access"},
            user=SimpleNamespace(identity="actor"),
        )

        self.assertFalse(asyncio.run(auth_module.create_run(regular, dict(value))))
        self.assertEqual(
            asyncio.run(auth_module.create_run(studio, dict(value))),
            {"owner_key": "actor"},
        )

    def test_run_authorization_allows_supervisor_and_denies_unknown_assistant(self) -> None:
        context = SimpleNamespace(
            permissions={"chat:run"},
            user=SimpleNamespace(identity="actor"),
        )
        supervisor = {
            "assistant_id": auth_module.assistant_id_for_graph("supervisor"),
            "kwargs": {},
        }
        unknown = {"assistant_id": "00000000-0000-0000-0000-000000000000"}

        self.assertEqual(
            asyncio.run(auth_module.create_run(context, supervisor)),
            {"owner_key": "actor"},
        )
        self.assertFalse(asyncio.run(auth_module.create_run(context, unknown)))


if __name__ == "__main__":
    unittest.main()
