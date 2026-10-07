"""Focused tests for confidential OIDC and opaque browser sessions."""

from __future__ import annotations

import asyncio
import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

from source.core.config import Settings
from source.infrastructure.browser_session_repository import (
    BrowserSessionRecord,
    OidcLoginTransaction,
)
from source.security import auth as auth_module
from source.security.browser_session import (
    BROWSER_SESSION_COOKIE,
    BrowserPrincipal,
    BrowserSessionManager,
    OidcConfiguration,
    SessionCipher,
    safe_return_to,
)
from source.security.context import actor_key_for_subject


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        deepseek_api_key="offline-test",  # pragma: allowlist secret
        nexus_oidc_issuer="https://identity.example.com",
        nexus_oidc_audience="nexus-api",
        nexus_oidc_client_id="nexus-bff",
        nexus_oidc_client_secret="c" * 32,  # pragma: allowlist secret
        nexus_app_origin="https://nexus.example.com",
        nexus_browser_session_auth="required",
        nexus_browser_session_key="s" * 32,  # pragma: allowlist secret
    )


def _configuration() -> OidcConfiguration:
    return OidcConfiguration(
        authorization_endpoint="https://identity.example.com/authorize",
        token_endpoint="https://identity.example.com/token",
        jwks_uri="https://identity.example.com/jwks",
        end_session_endpoint="https://identity.example.com/logout",
    )


class BrowserSessionManagerTests(unittest.TestCase):
    def test_cipher_binds_payload_to_aad_and_rejects_tampering(self) -> None:
        cipher = SessionCipher(b"s" * 32)
        encrypted = cipher.encrypt({"refresh_token": "private"}, aad=b"session-a")

        self.assertEqual(
            cipher.decrypt_json(encrypted, aad=b"session-a"),
            {"refresh_token": "private"},
        )
        with self.assertRaisesRegex(RuntimeError, "credentials are invalid"):
            cipher.decrypt_json(encrypted, aad=b"session-b")
        with self.assertRaisesRegex(RuntimeError, "credentials are invalid"):
            cipher.decrypt_json(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), aad=b"session-a")

    def test_return_path_rejects_external_and_auth_targets(self) -> None:
        self.assertEqual(safe_return_to("/evidence?status=completed"), "/evidence?status=completed")
        for unsafe in (
            "https://attacker.example",
            "//attacker.example",
            "/\\attacker.example",
            "/auth/callback",
            "/bad\r\npath",
        ):
            self.assertEqual(safe_return_to(unsafe), "/chat")

    def test_login_request_uses_pkce_s256_and_one_time_server_state(self) -> None:
        manager = BrowserSessionManager(
            object(),
            app_settings=_settings(),
            verify_access_token=AsyncMock(),
        )
        manager.repository = SimpleNamespace(create_login_transaction=AsyncMock())
        manager.discover = AsyncMock(return_value=_configuration())

        login = asyncio.run(manager.start_login("/evidence"))
        query = parse_qs(urlparse(login.authorization_url).query)

        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["response_mode"], ["form_post"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["https://nexus.example.com/auth/callback"])
        self.assertEqual(query["resource"], ["nexus-api"])
        self.assertNotIn("client_secret", query)
        stored = manager.repository.create_login_transaction.await_args.kwargs
        self.assertEqual(len(stored["state_hash"]), 32)
        self.assertEqual(len(stored["browser_binding_hash"]), 32)
        self.assertEqual(len(stored["nonce_hash"]), 32)
        self.assertEqual(stored["return_to"], "/evidence")
        self.assertGreaterEqual(len(login.browser_binding), 40)

    def test_callback_creates_only_an_opaque_browser_cookie_value(self) -> None:
        app_settings = _settings()
        verify_access = AsyncMock(return_value={
            "sub": "subject-1",
            "roles": ["analyst"],
            "exp": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
        })
        manager = BrowserSessionManager(
            object(),
            app_settings=app_settings,
            verify_access_token=verify_access,
        )
        nonce = "expected-nonce"
        manager.repository = SimpleNamespace(
            consume_login_transaction=AsyncMock(return_value=OidcLoginTransaction(
                nonce_hash=hashlib.sha256(nonce.encode()).digest(),
                verifier_ciphertext=manager.cipher.encrypt("pkce-verifier", aad=b"nexus-oidc-login-v1"),
                return_to="/chat",
            )),
            create_session=AsyncMock(),
        )
        manager.discover = AsyncMock(return_value=_configuration())
        manager._token_request = AsyncMock(return_value={
            "access_token": "private-access-token",
            "id_token": "private-id-token",
            "refresh_token": "private-refresh-token",
        })
        manager._verify_id_token = AsyncMock(return_value={"sub": "subject-1", "nonce": nonce})

        result = asyncio.run(manager.complete_login(
            state="one-time-state",
            code="authorization-code",
            browser_binding="b" * 43,
        ))

        self.assertEqual(result.return_to, "/chat")
        self.assertGreaterEqual(len(result.session_token), 40)
        self.assertNotIn("private", result.session_token)
        created = manager.repository.create_session.await_args.kwargs
        encrypted = created["credential_ciphertext"]
        self.assertNotIn(b"private-access-token", encrypted)
        self.assertNotIn(b"private-refresh-token", encrypted)
        self.assertIn("quant:experiment:run", created["permissions"])
        consumed = manager.repository.consume_login_transaction.await_args.args
        self.assertEqual(len(consumed[0]), 32)
        self.assertEqual(len(consumed[1]), 32)

    def test_callback_rejects_a_missing_browser_binding_before_consuming_state(self) -> None:
        manager = BrowserSessionManager(
            object(),
            app_settings=_settings(),
            verify_access_token=AsyncMock(),
        )
        manager.repository = SimpleNamespace(consume_login_transaction=AsyncMock())

        with self.assertRaisesRegex(RuntimeError, "callback parameters are invalid"):
            asyncio.run(manager.complete_login(
                state="one-time-state",
                code="authorization-code",
                browser_binding="",
            ))

        manager.repository.consume_login_transaction.assert_not_awaited()

    def test_active_session_returns_csrf_without_exposing_credentials(self) -> None:
        manager = BrowserSessionManager(
            object(),
            app_settings=_settings(),
            verify_access_token=AsyncMock(),
        )
        session_token = "x" * 43
        session_hash = hashlib.sha256(session_token.encode()).digest()
        record = BrowserSessionRecord(
            session_hash=session_hash,
            actor_key="v1-" + "a" * 64,
            permissions=("chat:run",),
            credential_ciphertext=b"unused",
            token_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        manager.repository = SimpleNamespace(get_active=AsyncMock(return_value=record))

        principal = asyncio.run(manager.resolve(session_token))

        self.assertEqual(principal.actor_key, record.actor_key)
        self.assertEqual(principal.permissions, ("chat:run",))
        self.assertEqual(principal.csrf_token, manager.csrf_token(session_token))
        self.assertNotEqual(principal.session_key, session_token)

    def test_expiring_session_rotates_refresh_credentials_server_side(self) -> None:
        app_settings = _settings()
        verify_access = AsyncMock(return_value={
            "sub": "subject-1",
            "roles": ["analyst"],
            "exp": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
        })
        manager = BrowserSessionManager(
            object(),
            app_settings=app_settings,
            verify_access_token=verify_access,
        )
        session_token = "r" * 43
        session_hash = hashlib.sha256(session_token.encode()).digest()
        actor_key = actor_key_for_subject("https://identity.example.com", "subject-1")
        credentials = {
            "subject": "subject-1",
            "id_token": "private-id-token",
            "refresh_token": "old-refresh-token",
        }
        expiring = BrowserSessionRecord(
            session_hash=session_hash,
            actor_key=actor_key,
            permissions=("chat:run",),
            credential_ciphertext=manager.cipher.encrypt(
                credentials,
                aad=b"nexus-browser-session-v1" + session_hash,
            ),
            token_expires_at=datetime.now(UTC) + timedelta(seconds=10),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        refreshed = BrowserSessionRecord(
            **{
                **expiring.__dict__,
                "permissions": ("chat:run", "quant:read"),
                "token_expires_at": datetime.now(UTC) + timedelta(minutes=10),
            }
        )
        manager.repository = SimpleNamespace(
            get_active=AsyncMock(return_value=expiring),
            rotate_credentials=AsyncMock(return_value=refreshed),
            revoke=AsyncMock(),
        )
        manager.discover = AsyncMock(return_value=_configuration())
        manager._token_request = AsyncMock(return_value={
            "access_token": "new-private-access-token",
            "refresh_token": "rotated-refresh-token",
            "id_token": "rotated-private-id-token",
        })
        manager._verify_id_token = AsyncMock(return_value={"sub": "subject-1"})

        principal = asyncio.run(manager.resolve(session_token))

        self.assertEqual(principal.actor_key, actor_key)
        request = manager._token_request.await_args.args[1]
        self.assertEqual(request, {
            "grant_type": "refresh_token",
            "refresh_token": "old-refresh-token",
        })
        rotated = manager.repository.rotate_credentials.await_args.kwargs
        self.assertNotIn(b"rotated-refresh-token", rotated["credential_ciphertext"])
        decrypted = manager.cipher.decrypt_json(
            rotated["credential_ciphertext"],
            aad=b"nexus-browser-session-v1" + session_hash,
        )
        self.assertEqual(decrypted["refresh_token"], "rotated-refresh-token")
        self.assertEqual(decrypted["id_token"], "rotated-private-id-token")
        self.assertFalse(manager._verify_id_token.await_args.kwargs["require_nonce"])


class BrowserSessionAuthTests(unittest.TestCase):
    def _request(self, *, origin: str = "https://nexus.example.com", csrf: str = "csrf"):
        return SimpleNamespace(
            cookies={BROWSER_SESSION_COOKIE: "x" * 43},
            headers={
                "origin": origin,
                "sec-fetch-site": "same-origin",
                "x-nexus-csrf": csrf,
            },
            state=SimpleNamespace(),
        )

    def test_cookie_auth_requires_origin_and_csrf_for_state_changes(self) -> None:
        principal = BrowserPrincipal(
            session_hash=b"h" * 32,
            actor_key="v1-" + "a" * 64,
            permissions=("chat:run",),
            session_key="public-session",
            csrf_token="csrf",
        )
        manager = Mock()
        manager.resolve = AsyncMock(return_value=principal)
        fake_settings = SimpleNamespace(
            nexus_env="production",
            nexus_oidc_issuer="https://identity.example.com",
            nexus_browser_session_auth="required",
            nexus_app_origin="https://nexus.example.com",
        )
        request = self._request()
        with (
            patch.object(auth_module, "settings", fake_settings),
            patch("source.infrastructure.runtime_resources.require_domain_pool", return_value=object()),
            patch("source.security.browser_session.BrowserSessionManager", return_value=manager),
        ):
            user = asyncio.run(auth_module.authenticate(None, "/threads", "POST", request))

        manager.validate_csrf.assert_called_once_with("x" * 43, "csrf")
        self.assertEqual(user["identity"], principal.actor_key)
        self.assertIs(request.state.browser_principal, principal)

    def test_form_post_callback_is_public_bootstrap_only(self) -> None:
        user = asyncio.run(auth_module.authenticate(None, "/auth/callback", "POST"))

        self.assertEqual(user, {"identity": "oidc-bootstrap", "permissions": []})

    def test_cookie_auth_rejects_cross_origin_and_required_mode_rejects_bearer_fallback(self) -> None:
        fake_settings = SimpleNamespace(
            nexus_env="production",
            nexus_oidc_issuer="https://identity.example.com",
            nexus_browser_session_auth="required",
            nexus_app_origin="https://nexus.example.com",
        )
        manager = Mock()
        with (
            patch.object(auth_module, "settings", fake_settings),
            patch("source.infrastructure.runtime_resources.require_domain_pool", return_value=object()),
            patch("source.security.browser_session.BrowserSessionManager", return_value=manager),
        ):
            with self.assertRaises(auth_module.Auth.exceptions.HTTPException) as cross_origin:
                asyncio.run(auth_module.authenticate(None, "/threads", "POST", self._request(origin="https://attacker.example")))
            no_cookie = SimpleNamespace(cookies={}, headers={}, state=SimpleNamespace())
            with self.assertRaises(auth_module.Auth.exceptions.HTTPException) as bearer:
                asyncio.run(auth_module.authenticate("Bearer old-spa-token", "/threads", "GET", no_cookie))

        self.assertEqual(cross_origin.exception.status_code, 403)
        self.assertEqual(bearer.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
