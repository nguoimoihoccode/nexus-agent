"""Server-side OIDC authorization-code flow and opaque browser sessions."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from source.core.config import Settings, settings
from source.infrastructure.browser_session_repository import (
    BrowserSessionRecord,
    PostgresBrowserSessionRepository,
)
from source.security.context import actor_key_for_subject, permissions_for_roles

BROWSER_SESSION_COOKIE = "__Host-nexus_session"
BROWSER_LOGIN_COOKIE = "__Host-nexus_login"
_LOGIN_AAD = b"nexus-oidc-login-v1"
_SESSION_AAD = b"nexus-browser-session-v1"


class BrowserSessionError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class BrowserPrincipal:
    session_hash: bytes
    actor_key: str
    permissions: tuple[str, ...]
    session_key: str
    csrf_token: str


@dataclass(frozen=True)
class LoginResult:
    session_token: str
    return_to: str


@dataclass(frozen=True)
class LoginStart:
    authorization_url: str
    browser_binding: str


@dataclass(frozen=True)
class OidcConfiguration:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None


def _hash(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def safe_return_to(value: str | None) -> str:
    target = str(value or "/chat")
    if (
        not target.startswith("/")
        or target.startswith("//")
        or target.startswith("/auth/")
        or len(target) > 2048
        or "\\" in target
        or any(ord(character) < 32 or ord(character) == 127 for character in target)
    ):
        return "/chat"
    return target


class SessionCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Browser session encryption key must be exactly 32 bytes.")
        self.key = key

    def encrypt(self, value: dict[str, Any] | str, *, aad: bytes) -> bytes:
        payload = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), sort_keys=True)
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, payload.encode(), aad)

    def decrypt_text(self, payload: bytes, *, aad: bytes) -> str:
        if len(payload) < 29:
            raise BrowserSessionError(401, "Browser session credentials are invalid.")
        nonce, ciphertext = payload[:12], payload[12:]
        try:
            return AESGCM(self.key).decrypt(nonce, ciphertext, aad).decode()
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise BrowserSessionError(401, "Browser session credentials are invalid.") from exc

    def decrypt_json(self, payload: bytes, *, aad: bytes) -> dict[str, Any]:
        try:
            value = json.loads(self.decrypt_text(payload, aad=aad))
        except json.JSONDecodeError as exc:
            raise BrowserSessionError(401, "Browser session credentials are invalid.") from exc
        if not isinstance(value, dict):
            raise BrowserSessionError(401, "Browser session credentials are invalid.")
        return value


@lru_cache(maxsize=4)
def _jwk_client(jwks_uri: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(jwks_uri, cache_keys=True, cache_jwk_set=True, lifespan=300)


class BrowserSessionManager:
    def __init__(
        self,
        pool: Any,
        *,
        app_settings: Settings = settings,
        verify_access_token: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> None:
        key = app_settings.nexus_browser_session_key
        if key is None:
            raise BrowserSessionError(503, "Browser session authentication is not configured.")
        self.settings = app_settings
        self.repository = PostgresBrowserSessionRepository(pool)
        self.verify_access_token = verify_access_token
        self.cipher = SessionCipher(key.get_secret_value().encode())

    async def discover(self) -> OidcConfiguration:
        issuer = str(self.settings.nexus_oidc_issuer or "").rstrip("/")
        if not issuer.startswith("https://"):
            raise BrowserSessionError(503, "OIDC issuer is not configured.")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{issuer}/.well-known/openid-configuration")
                response.raise_for_status()
                raw = response.json()
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise BrowserSessionError(503, "OIDC discovery is unavailable.") from exc
        if not isinstance(raw, dict) or str(raw.get("issuer") or "").rstrip("/") != issuer:
            raise BrowserSessionError(503, "OIDC discovery returned an invalid issuer.")
        endpoints = [raw.get("authorization_endpoint"), raw.get("token_endpoint"), raw.get("jwks_uri")]
        if any(not isinstance(value, str) or not value.startswith("https://") for value in endpoints):
            raise BrowserSessionError(503, "OIDC discovery returned an insecure endpoint.")
        end_session = raw.get("end_session_endpoint")
        if end_session is not None and (not isinstance(end_session, str) or not end_session.startswith("https://")):
            raise BrowserSessionError(503, "OIDC discovery returned an insecure logout endpoint.")
        return OidcConfiguration(
            authorization_endpoint=str(endpoints[0]),
            token_endpoint=str(endpoints[1]),
            jwks_uri=str(endpoints[2]),
            end_session_endpoint=end_session,
        )

    async def start_login(self, return_to: str | None) -> LoginStart:
        config = await self.discover()
        state = _b64(secrets.token_bytes(32))
        browser_binding = _b64(secrets.token_bytes(32))
        nonce = _b64(secrets.token_bytes(32))
        verifier = _b64(secrets.token_bytes(48))
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        await self.repository.create_login_transaction(
            state_hash=_hash(state),
            browser_binding_hash=_hash(browser_binding),
            nonce_hash=_hash(nonce),
            verifier_ciphertext=self.cipher.encrypt(verifier, aad=_LOGIN_AAD),
            return_to=safe_return_to(return_to),
        )
        query = {
            "response_type": "code",
            "response_mode": "form_post",
            "client_id": str(self.settings.nexus_oidc_client_id),
            "redirect_uri": f"{self.settings.nexus_app_origin}/auth/callback",
            "scope": self.settings.nexus_oidc_scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self.settings.nexus_oidc_audience:
            query["resource"] = self.settings.nexus_oidc_audience
        return LoginStart(
            authorization_url=f"{config.authorization_endpoint}?{urlencode(query)}",
            browser_binding=browser_binding,
        )

    async def complete_login(
        self,
        *,
        state: str,
        code: str,
        browser_binding: str,
    ) -> LoginResult:
        if (
            len(state) > 256
            or len(code) > 4096
            or not 40 <= len(browser_binding) <= 128
            or not state
            or not code
        ):
            raise BrowserSessionError(400, "OIDC callback parameters are invalid.")
        transaction = await self.repository.consume_login_transaction(
            _hash(state),
            _hash(browser_binding),
        )
        if transaction is None:
            raise BrowserSessionError(400, "OIDC login transaction is missing or expired.")
        verifier = self.cipher.decrypt_text(transaction.verifier_ciphertext, aad=_LOGIN_AAD)
        config = await self.discover()
        token_response = await self._token_request(
            config.token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{self.settings.nexus_app_origin}/auth/callback",
                "code_verifier": verifier,
            },
        )
        access_token = token_response.get("access_token")
        id_token = token_response.get("id_token")
        if not isinstance(access_token, str) or not isinstance(id_token, str):
            raise BrowserSessionError(401, "OIDC token response is incomplete.")
        access_claims = await self.verify_access_token(access_token)
        id_claims = await self._verify_id_token(id_token, config.jwks_uri)
        nonce = id_claims.get("nonce")
        if not isinstance(nonce, str) or not hmac.compare_digest(_hash(nonce), transaction.nonce_hash):
            raise BrowserSessionError(401, "OIDC nonce validation failed.")
        subject = str(access_claims.get("sub") or "")
        if not subject or subject != str(id_claims.get("sub") or ""):
            raise BrowserSessionError(401, "OIDC subject validation failed.")
        actor_key, permissions = self._identity(access_claims)
        token_expires_at = self._token_expiry(access_claims)
        session_token = _b64(secrets.token_bytes(32))
        session_hash = _hash(session_token)
        refresh_token = token_response.get("refresh_token")
        credentials = {
            "subject": subject,
            "id_token": id_token,
            "refresh_token": refresh_token if isinstance(refresh_token, str) else None,
        }
        remaining = max(1, int((token_expires_at - datetime.now(UTC)).total_seconds()))
        absolute_seconds = self.settings.nexus_browser_session_absolute_seconds
        if not credentials["refresh_token"]:
            absolute_seconds = min(absolute_seconds, remaining)
        idle_seconds = min(self.settings.nexus_browser_session_idle_seconds, absolute_seconds)
        await self.repository.create_session(
            session_hash=session_hash,
            actor_key=actor_key,
            permissions=permissions,
            credential_ciphertext=self.cipher.encrypt(
                credentials,
                aad=_SESSION_AAD + session_hash,
            ),
            token_expires_at=token_expires_at,
            idle_seconds=idle_seconds,
            absolute_seconds=absolute_seconds,
        )
        return LoginResult(session_token=session_token, return_to=transaction.return_to)

    async def resolve(self, session_token: str) -> BrowserPrincipal:
        if not 40 <= len(session_token) <= 128:
            raise BrowserSessionError(401, "Browser session is required.")
        session_hash = _hash(session_token)
        record = await self.repository.get_active(
            session_hash,
            idle_seconds=self.settings.nexus_browser_session_idle_seconds,
        )
        if record is None:
            raise BrowserSessionError(401, "Browser session is expired or revoked.")
        record = await self._refresh_if_needed(record)
        return BrowserPrincipal(
            session_hash=session_hash,
            actor_key=record.actor_key,
            permissions=record.permissions,
            session_key=_b64(record.session_hash[:18]),
            csrf_token=self.csrf_token(session_token),
        )

    def csrf_token(self, session_token: str) -> str:
        return _b64(hmac.new(self.cipher.key, b"csrf:" + session_token.encode(), hashlib.sha256).digest())

    def validate_csrf(self, session_token: str, supplied: str | None) -> None:
        expected = self.csrf_token(session_token)
        if not supplied or not hmac.compare_digest(expected, supplied):
            raise BrowserSessionError(403, "CSRF validation failed.")

    async def logout(self, session_token: str) -> str | None:
        session_hash = _hash(session_token)
        record = await self.repository.get_active(
            session_hash,
            idle_seconds=self.settings.nexus_browser_session_idle_seconds,
        )
        if record is None:
            return None
        credentials = self.cipher.decrypt_json(
            record.credential_ciphertext,
            aad=_SESSION_AAD + session_hash,
        )
        await self.repository.revoke(session_hash, reason="user_logout")
        try:
            config = await self.discover()
        except BrowserSessionError:
            return None
        if not config.end_session_endpoint:
            return None
        query = {
            "post_logout_redirect_uri": str(self.settings.nexus_app_origin),
            "client_id": str(self.settings.nexus_oidc_client_id),
        }
        if isinstance(credentials.get("id_token"), str):
            query["id_token_hint"] = credentials["id_token"]
        return f"{config.end_session_endpoint}?{urlencode(query)}"

    async def _refresh_if_needed(self, record: BrowserSessionRecord) -> BrowserSessionRecord:
        now = datetime.now(UTC)
        if record.token_expires_at > now and (record.token_expires_at - now).total_seconds() > 60:
            return record
        credentials = self.cipher.decrypt_json(
            record.credential_ciphertext,
            aad=_SESSION_AAD + record.session_hash,
        )
        refresh_token = credentials.get("refresh_token")
        if not isinstance(refresh_token, str):
            if record.token_expires_at > now:
                return record
            await self.repository.revoke(record.session_hash, reason="expired_token")
            raise BrowserSessionError(401, "Browser session token has expired.")
        try:
            config = await self.discover()
            token_response = await self._token_request(
                config.token_endpoint,
                {"grant_type": "refresh_token", "refresh_token": refresh_token},
            )
            access_token = token_response.get("access_token")
            if not isinstance(access_token, str):
                raise BrowserSessionError(401, "OIDC refresh response is incomplete.")
            claims = await self.verify_access_token(access_token)
            actor_key, permissions = self._identity(claims)
            if actor_key != record.actor_key or str(claims.get("sub") or "") != credentials.get("subject"):
                raise BrowserSessionError(401, "OIDC refreshed identity changed.")
            next_refresh = token_response.get("refresh_token")
            if isinstance(next_refresh, str):
                credentials["refresh_token"] = next_refresh
            next_id_token = token_response.get("id_token")
            if isinstance(next_id_token, str):
                id_claims = await self._verify_id_token(
                    next_id_token,
                    config.jwks_uri,
                    require_nonce=False,
                )
                if str(id_claims.get("sub") or "") != credentials.get("subject"):
                    raise BrowserSessionError(401, "OIDC refreshed subject changed.")
                credentials["id_token"] = next_id_token
            updated = await self.repository.rotate_credentials(
                session_hash=record.session_hash,
                actor_key=record.actor_key,
                permissions=permissions,
                credential_ciphertext=self.cipher.encrypt(
                    credentials,
                    aad=_SESSION_AAD + record.session_hash,
                ),
                token_expires_at=self._token_expiry(claims),
            )
            if updated is None:
                raise BrowserSessionError(401, "Browser session was revoked during refresh.")
            return updated
        except BrowserSessionError:
            await self.repository.revoke(record.session_hash, reason="refresh_failed")
            raise
        except Exception as exc:
            await self.repository.revoke(record.session_hash, reason="refresh_failed")
            raise BrowserSessionError(401, "OIDC session refresh failed.") from exc

    async def _token_request(self, endpoint: str, data: dict[str, str]) -> dict[str, Any]:
        secret = self.settings.nexus_oidc_client_secret
        if secret is None:
            raise BrowserSessionError(503, "OIDC confidential client is not configured.")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    endpoint,
                    data=data,
                    auth=(str(self.settings.nexus_oidc_client_id), secret.get_secret_value()),
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise BrowserSessionError(401, "OIDC token exchange failed.") from exc
        if not isinstance(payload, dict):
            raise BrowserSessionError(401, "OIDC token response is invalid.")
        return payload

    async def _verify_id_token(
        self,
        token: str,
        jwks_uri: str,
        *,
        require_nonce: bool = True,
    ) -> dict[str, Any]:
        required_claims = ["exp", "iat", "sub"]
        if require_nonce:
            required_claims.append("nonce")
        try:
            signing_key = await asyncio.to_thread(_jwk_client(jwks_uri).get_signing_key_from_jwt, token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=self.settings.nexus_oidc_allowed_algorithms,
                audience=self.settings.nexus_oidc_client_id,
                issuer=self.settings.nexus_oidc_issuer,
                options={"require": required_claims},
            )
        except jwt.PyJWTError as exc:
            raise BrowserSessionError(401, "OIDC ID token is invalid.") from exc

    def _identity(self, claims: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
        subject = str(claims.get("sub") or "").strip()
        raw_roles = claims.get(self.settings.nexus_oidc_roles_claim, [])
        roles = [raw_roles] if isinstance(raw_roles, str) else raw_roles
        if not subject or not isinstance(roles, list):
            raise BrowserSessionError(401, "OIDC access token identity is invalid.")
        permissions = tuple(sorted(permissions_for_roles(str(role) for role in roles)))
        if "chat:run" not in permissions:
            raise BrowserSessionError(403, "A recognized Nexus role is required.")
        return actor_key_for_subject(str(self.settings.nexus_oidc_issuer), subject), permissions

    @staticmethod
    def _token_expiry(claims: dict[str, Any]) -> datetime:
        try:
            expires = datetime.fromtimestamp(int(claims["exp"]), UTC)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise BrowserSessionError(401, "OIDC access token expiry is invalid.") from exc
        if expires <= datetime.now(UTC):
            raise BrowserSessionError(401, "OIDC access token has expired.")
        return expires


__all__ = [
    "BROWSER_LOGIN_COOKIE",
    "BROWSER_SESSION_COOKIE",
    "BrowserPrincipal",
    "BrowserSessionError",
    "BrowserSessionManager",
    "LoginResult",
    "LoginStart",
    "SessionCipher",
    "safe_return_to",
]
