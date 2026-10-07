"""Opaque browser sessions and one-time OIDC login transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class OidcLoginTransaction:
    nonce_hash: bytes
    verifier_ciphertext: bytes
    return_to: str


@dataclass(frozen=True)
class BrowserSessionRecord:
    session_hash: bytes
    actor_key: str
    permissions: tuple[str, ...]
    credential_ciphertext: bytes
    token_expires_at: datetime
    expires_at: datetime


def _session(row: Any) -> BrowserSessionRecord:
    return BrowserSessionRecord(
        session_hash=bytes(row[0]),
        actor_key=str(row[1]),
        permissions=tuple(str(item) for item in row[2]),
        credential_ciphertext=bytes(row[3]),
        token_expires_at=row[4],
        expires_at=row[5],
    )


class PostgresBrowserSessionRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def create_login_transaction(
        self,
        *,
        state_hash: bytes,
        browser_binding_hash: bytes,
        nonce_hash: bytes,
        verifier_ciphertext: bytes,
        return_to: str,
        ttl_seconds: int = 600,
    ) -> None:
        if (
            len(state_hash) != 32
            or len(browser_binding_hash) != 32
            or len(nonce_hash) != 32
        ):
            raise ValueError("OIDC state, browser binding, and nonce hashes must be SHA-256 values.")
        if not 60 <= ttl_seconds <= 600:
            raise ValueError("OIDC login transaction TTL is invalid.")
        if (
            not return_to.startswith("/")
            or return_to.startswith("//")
            or "\\" in return_to
            or len(return_to) > 2048
        ):
            raise ValueError("OIDC return path is invalid.")
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "DELETE FROM nexus_domain.oidc_login_transactions WHERE expires_at <= now()"
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.oidc_login_transactions
                        (state_hash, browser_binding_hash, nonce_hash,
                         verifier_ciphertext, return_to, expires_at)
                    VALUES (%s, %s, %s, %s, %s, now() + (%s * interval '1 second'))
                    """,
                    (
                        state_hash,
                        browser_binding_hash,
                        nonce_hash,
                        verifier_ciphertext,
                        return_to,
                        ttl_seconds,
                    ),
                )

    async def consume_login_transaction(
        self,
        state_hash: bytes,
        browser_binding_hash: bytes,
    ) -> OidcLoginTransaction | None:
        if len(state_hash) != 32 or len(browser_binding_hash) != 32:
            return None
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM nexus_domain.oidc_login_transactions
                WHERE state_hash = %s
                  AND browser_binding_hash = %s
                  AND expires_at > now()
                RETURNING nonce_hash, verifier_ciphertext, return_to
                """,
                (state_hash, browser_binding_hash),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return OidcLoginTransaction(
            nonce_hash=bytes(row[0]),
            verifier_ciphertext=bytes(row[1]),
            return_to=str(row[2]),
        )

    async def create_session(
        self,
        *,
        session_hash: bytes,
        actor_key: str,
        permissions: tuple[str, ...],
        credential_ciphertext: bytes,
        token_expires_at: datetime,
        idle_seconds: int,
        absolute_seconds: int,
    ) -> BrowserSessionRecord:
        if len(session_hash) != 32:
            raise ValueError("Browser session hash must be a SHA-256 value.")
        if not permissions or len(permissions) > 64:
            raise ValueError("Browser session permissions are invalid.")
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    DELETE FROM nexus_domain.browser_sessions
                    WHERE revoked_at IS NOT NULL OR expires_at <= now()
                       OR idle_expires_at <= now()
                    """
                )
                cursor = await connection.execute(
                    """
                    INSERT INTO nexus_domain.browser_sessions
                        (session_hash, actor_key, permissions, credential_ciphertext,
                         token_expires_at, idle_expires_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s,
                            now() + (%s * interval '1 second'),
                            now() + (%s * interval '1 second'))
                    RETURNING session_hash, actor_key, permissions,
                              credential_ciphertext, token_expires_at, expires_at
                    """,
                    (
                        session_hash,
                        actor_key,
                        list(permissions),
                        credential_ciphertext,
                        token_expires_at,
                        idle_seconds,
                        absolute_seconds,
                    ),
                )
                return _session(await cursor.fetchone())

    async def get_active(
        self,
        session_hash: bytes,
        *,
        idle_seconds: int,
    ) -> BrowserSessionRecord | None:
        if len(session_hash) != 32:
            return None
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE nexus_domain.browser_sessions
                SET last_seen_at = now(),
                    idle_expires_at = LEAST(
                        expires_at,
                        now() + (%s * interval '1 second')
                    )
                WHERE session_hash = %s
                  AND revoked_at IS NULL
                  AND expires_at > now()
                  AND idle_expires_at > now()
                RETURNING session_hash, actor_key, permissions,
                          credential_ciphertext, token_expires_at, expires_at
                """,
                (idle_seconds, session_hash),
            )
            row = await cursor.fetchone()
        return _session(row) if row is not None else None

    async def rotate_credentials(
        self,
        *,
        session_hash: bytes,
        actor_key: str,
        permissions: tuple[str, ...],
        credential_ciphertext: bytes,
        token_expires_at: datetime,
    ) -> BrowserSessionRecord | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE nexus_domain.browser_sessions
                SET permissions = %s,
                    credential_ciphertext = %s,
                    token_expires_at = %s
                WHERE session_hash = %s AND actor_key = %s
                  AND revoked_at IS NULL AND expires_at > now()
                RETURNING session_hash, actor_key, permissions,
                          credential_ciphertext, token_expires_at, expires_at
                """,
                (
                    list(permissions),
                    credential_ciphertext,
                    token_expires_at,
                    session_hash,
                    actor_key,
                ),
            )
            row = await cursor.fetchone()
        return _session(row) if row is not None else None

    async def revoke(self, session_hash: bytes, *, reason: str = "user_logout") -> bool:
        if reason not in {"user_logout", "expired_token", "refresh_failed", "security_event"}:
            raise ValueError("Browser session revocation reason is invalid.")
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE nexus_domain.browser_sessions
                SET revoked_at = now(), revocation_reason = %s
                WHERE session_hash = %s AND revoked_at IS NULL
                RETURNING session_hash
                """,
                (reason, session_hash),
            )
            return await cursor.fetchone() is not None


__all__ = [
    "BrowserSessionRecord",
    "OidcLoginTransaction",
    "PostgresBrowserSessionRepository",
]
