from __future__ import annotations

import asyncio
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi import Response
from starlette.authentication import AuthCredentials

from source.http_app import (
    AuthorizationLeaseInput,
    _identity,
    _require_permission,
    app,
    create_authorization_lease,
    experiment_catalog,
    get_authorization_lease,
    get_user_memory,
    revoke_authorization_lease,
    browser_callback,
    browser_login,
    browser_logout,
    browser_session,
)
from source.domain import AuthorizationLease
from source.security.browser_session import (
    BROWSER_LOGIN_COOKIE,
    BROWSER_SESSION_COOKIE,
    LoginResult,
    LoginStart,
)


class ProductHttpRouteTests(unittest.TestCase):
    def test_langgraph_file_loader_can_generate_openapi(self) -> None:
        """Keep route annotations resolvable under LangGraph's dynamic module name."""
        path = Path(__file__).parents[1] / "source" / "http_app.py"
        spec = importlib.util.spec_from_file_location("user_router_module", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)

        spec.loader.exec_module(module)
        schema = module.app.openapi()

        self.assertIn("/v1/authorization-leases", schema["paths"])

    def test_product_app_does_not_expose_schema_documentation_routes(self) -> None:
        paths = {route.path for route in app.routes}

        self.assertNotIn("/docs", paths)
        self.assertNotIn("/redoc", paths)
        self.assertNotIn("/openapi.json", paths)
        self.assertIn("/v1/memory", paths)
        self.assertIn("/v1/authorization-leases", paths)
        self.assertIn("/v1/authorization-leases/current", paths)
        self.assertIn("/v1/authorization-leases/{lease_id}", paths)
        self.assertIn("/v1/operations/readiness", paths)
        self.assertIn("/v1/experiments", paths)
        self.assertIn("/auth/login", paths)
        self.assertIn("/auth/callback", paths)
        self.assertIn("/auth/session", paths)
        self.assertIn("/auth/logout", paths)
        self.assertIn("/auth/csp-report", paths)
        for removed_path in (
            "/v1/operations/metrics",
            "/v1/runs/{run_id}/sources",
            "/v1/runs/{run_id}/approvals",
            "/v1/datasets",
            "/v1/experiments/{experiment_id}",
            "/v1/experiments/{experiment_id}/cancel",
            "/v1/artifacts/{artifact_id}",
        ):
            self.assertNotIn(removed_path, paths)
        self.assertNotIn("/store" + "/items", paths)

    def test_browser_callback_sets_session_cookie_and_clears_login_binding(self) -> None:
        manager = SimpleNamespace(
            complete_login=AsyncMock(return_value=LoginResult(
                session_token="opaque-session-token",
                return_to="/evidence",
            ))
        )
        fake_settings = SimpleNamespace(
            nexus_browser_session_auth="required",
            nexus_browser_session_absolute_seconds=28_800,
            nexus_env="production",
        )
        with (
            patch("source.http_app.settings", fake_settings),
            patch("source.http_app._browser_session_manager", return_value=manager),
        ):
            request = SimpleNamespace(
                headers={"content-type": "application/x-www-form-urlencoded"},
                cookies={BROWSER_LOGIN_COOKIE: "b" * 43},
                body=AsyncMock(return_value=b"state=state&code=code"),
            )
            response = asyncio.run(browser_callback(request))

        cookie = response.headers["set-cookie"]
        self.assertIn(f"{BROWSER_SESSION_COOKIE}=opaque-session-token", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertEqual(response.headers["location"], "/evidence")
        manager.complete_login.assert_awaited_once_with(
            state="state",
            code="code",
            browser_binding="b" * 43,
        )
        all_cookies = b"\n".join(
            value for name, value in response.raw_headers if name == b"set-cookie"
        ).decode()
        self.assertIn(f"{BROWSER_LOGIN_COOKIE}=", all_cookies)
        self.assertIn("Max-Age=0", all_cookies)

    def test_browser_login_binds_the_transaction_to_a_temporary_cookie(self) -> None:
        manager = SimpleNamespace(start_login=AsyncMock(return_value=LoginStart(
            authorization_url="https://identity.example.com/authorize?state=opaque",
            browser_binding="b" * 43,
        )))
        fake_settings = SimpleNamespace(
            nexus_browser_session_auth="required",
            nexus_env="production",
        )
        with (
            patch("source.http_app.settings", fake_settings),
            patch("source.http_app._browser_session_manager", return_value=manager),
        ):
            response = asyncio.run(browser_login("/evidence"))

        self.assertEqual(
            response.headers["location"],
            "https://identity.example.com/authorize?state=opaque",
        )
        cookie = response.headers["set-cookie"]
        self.assertIn(f"{BROWSER_LOGIN_COOKIE}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=none", cookie)
        self.assertNotIn("Domain=", cookie)

    def test_browser_callback_rejects_query_style_or_ambiguous_fields(self) -> None:
        fake_settings = SimpleNamespace(nexus_browser_session_auth="required")
        requests = (
            SimpleNamespace(
                headers={"content-type": "application/json"},
                cookies={},
                body=AsyncMock(return_value=b"{}"),
            ),
            SimpleNamespace(
                headers={"content-type": "application/x-www-form-urlencoded"},
                cookies={},
                body=AsyncMock(return_value=b"state=one&state=two&code=code"),
            ),
            SimpleNamespace(
                headers={"content-type": "application/x-www-form-urlencoded"},
                cookies={},
                body=AsyncMock(return_value=b"state=state&code=code&error=unexpected"),
            ),
        )
        with patch("source.http_app.settings", fake_settings):
            for request in requests:
                with self.subTest(request=request), self.assertRaises(HTTPException) as raised:
                    asyncio.run(browser_callback(request))
                self.assertIn(raised.exception.status_code, {400, 415})

    def test_browser_session_and_logout_are_no_store_and_clear_client_state(self) -> None:
        principal = SimpleNamespace(session_key="public-key", csrf_token="csrf")
        request = SimpleNamespace(
            state=SimpleNamespace(browser_principal=principal),
            cookies={BROWSER_SESSION_COOKIE: "opaque-session-token"},
        )
        manager = SimpleNamespace(logout=AsyncMock(return_value=None))
        fake_settings = SimpleNamespace(
            nexus_browser_session_auth="required",
            nexus_env="production",
        )
        with (
            patch("source.http_app.settings", fake_settings),
            patch("source.http_app._browser_session_manager", return_value=manager),
        ):
            session_response = Response()
            payload = asyncio.run(browser_session(request, session_response))
            logout_response = asyncio.run(browser_logout(request))

        self.assertEqual(payload["session_key"], "public-key")
        self.assertEqual(payload["csrf_token"], "csrf")
        self.assertEqual(session_response.headers["cache-control"], "no-store, private")
        self.assertEqual(logout_response.headers["clear-site-data"], '"cache", "cookies", "storage"')
        self.assertIn(f"{BROWSER_SESSION_COOKIE}=", logout_response.headers["set-cookie"])

    def test_memory_route_is_actor_scoped_and_read_only(self) -> None:
        actor = "v1-" + "c" * 64
        request = SimpleNamespace(
            scope={
                "user": SimpleNamespace(identity=actor),
                "auth": ["chat:run"],
            }
        )
        repository = SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    content="durable preference",
                    revision=3,
                    updated_at="2026-07-15T00:00:00Z",
                )
            ),
        )
        with (
            patch("source.http_app.require_domain_pool", return_value=object()),
            patch(
                "source.http_app.PostgresUserMemoryRepository",
                return_value=repository,
            ),
        ):
            memory_response = Response()
            loaded = asyncio.run(get_user_memory(request, memory_response))

        self.assertEqual(loaded["content"], "durable preference")
        self.assertEqual(loaded["revision"], 3)
        self.assertEqual(memory_response.headers["cache-control"], "no-store, private")
        repository.get.assert_awaited_once()
        memory_route = next(route for route in app.routes if route.path == "/v1/memory")
        self.assertEqual(memory_route.methods, {"GET"})

    def test_experiment_catalog_is_actor_scoped_private_and_read_only(self) -> None:
        actor = "v1-" + "e" * 64
        request = SimpleNamespace(
            scope={
                "user": SimpleNamespace(identity=actor),
                "auth": ["quant:read"],
            }
        )
        repository = SimpleNamespace(
            list_experiments=AsyncMock(
                return_value={"schema_version": "1", "items": [], "next_cursor": None}
            )
        )
        with (
            patch("source.http_app.require_domain_pool", return_value=object()),
            patch(
                "source.http_app.PostgresExperimentCatalogRepository",
                return_value=repository,
            ),
        ):
            response = Response()
            loaded = asyncio.run(
                experiment_catalog(
                    request,
                    response,
                    limit=10,
                    cursor=None,
                    status=["completed", "completed"],
                )
            )

        self.assertEqual(loaded["items"], [])
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        repository.list_experiments.assert_awaited_once_with(
            actor_key=actor,
            limit=10,
            statuses=("completed",),
            cursor=None,
        )
        route = next(route for route in app.routes if route.path == "/v1/experiments")
        self.assertEqual(route.methods, {"GET"})

    def test_experiment_catalog_rejects_unknown_status(self) -> None:
        request = SimpleNamespace(
            scope={
                "user": SimpleNamespace(identity="v1-" + "f" * 64),
                "auth": ["quant:read"],
            }
        )

        with self.assertRaises(HTTPException) as invalid:
            asyncio.run(
                experiment_catalog(
                    request,
                    Response(),
                    limit=20,
                    cursor=None,
                    status=["unknown"],
                )
            )

        self.assertEqual(invalid.exception.status_code, 422)

    def test_identity_reads_authenticated_actor_and_permissions(self) -> None:
        actor = "v1-" + "a" * 64
        request = SimpleNamespace(
            scope={
                "user": SimpleNamespace(identity=actor),
                "auth": ["chat:run", "quant:read"],
            }
        )

        identity, permissions = _identity(request)

        self.assertEqual(identity, actor)
        self.assertEqual(permissions, {"chat:run", "quant:read"})

    def test_authorization_lease_routes_are_actor_scoped_and_revocable(self) -> None:
        actor = "v1-" + "d" * 64
        request = SimpleNamespace(
            scope={
                "user": SimpleNamespace(identity=actor),
                "auth": ["chat:run"],
            }
        )
        now = datetime.now(timezone.utc)
        lease = AuthorizationLease(
            actor_key=actor,
            lease_id="azl_v1_" + "1" * 32,
            thread_id="thread-permission",
            mode="full_access",
            allow_sensitive=False,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        repository = SimpleNamespace(
            get_active=AsyncMock(return_value=lease),
            create=AsyncMock(return_value=lease),
            revoke=AsyncMock(return_value=True),
        )
        with (
            patch("source.http_app.require_domain_pool", return_value=object()),
            patch(
                "source.http_app.PostgresAuthorizationLeaseRepository",
                return_value=repository,
            ),
        ):
            get_response = Response()
            loaded = asyncio.run(
                get_authorization_lease(
                    request,
                    get_response,
                    thread_id="thread-permission",
                )
            )
            create_response = Response()
            created = asyncio.run(
                create_authorization_lease(
                    AuthorizationLeaseInput(
                        thread_id="thread-permission",
                        mode="full_access",
                        ttl_seconds=3600,
                        allow_sensitive=False,
                    ),
                    request,
                    create_response,
                )
            )
            revoked = asyncio.run(
                revoke_authorization_lease(request, lease.lease_id)
            )

        self.assertEqual(loaded["mode"], "full_access")
        self.assertIn("save_user_memory", loaded["allowed_tools"])
        self.assertNotIn("delete_user_memory", loaded["allowed_tools"])
        self.assertEqual(created["lease_id"], lease.lease_id)
        self.assertEqual(get_response.headers["cache-control"], "no-store, private")
        self.assertEqual(revoked.status_code, 204)
        repository.get_active.assert_awaited_once_with(
            actor_key=actor,
            thread_id="thread-permission",
        )
        repository.revoke.assert_awaited_once_with(
            actor_key=actor,
            lease_id=lease.lease_id,
        )

    def test_authorization_input_rejects_sensitive_autonomous_mode(self) -> None:
        with self.assertRaises(ValueError):
            AuthorizationLeaseInput(
                thread_id="thread-permission",
                mode="autonomous",
                ttl_seconds=3600,
                allow_sensitive=True,
            )

    def test_identity_reads_starlette_auth_credentials(self) -> None:
        actor = "v1-" + "b" * 64
        request = SimpleNamespace(
            scope={
                "user": SimpleNamespace(identity=actor),
                "auth": AuthCredentials(["chat:run"]),
            }
        )

        identity, permissions = _identity(request)

        self.assertEqual(identity, actor)
        self.assertEqual(permissions, {"chat:run"})

    def test_identity_and_permission_fail_closed(self) -> None:
        request = SimpleNamespace(
            scope={"user": SimpleNamespace(identity="development"), "auth": []}
        )

        with self.assertRaises(HTTPException) as unauthenticated:
            _identity(request)
        with self.assertRaises(HTTPException) as forbidden:
            _require_permission(frozenset({"chat:run"}), "quant:read")

        self.assertEqual(unauthenticated.exception.status_code, 401)
        self.assertEqual(forbidden.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
