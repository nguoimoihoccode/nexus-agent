"""PostgreSQL authority for actor- and thread-scoped authorization leases."""

from __future__ import annotations

import re
from typing import Any

from source.contracts.identity import new_runtime_id
from source.domain import AuthorizationLease, AuthorizationMode

_ACTOR_KEY = re.compile(r"^v1-[0-9a-f]{64}$")
_THREAD_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_LEASE_ID = re.compile(r"^azl_v1_[0-9a-f]{32}$")
_MODES = frozenset({"autonomous", "full_access"})
_REVOCATION_REASONS = frozenset({"replaced", "user_revoked", "thread_cleared"})


def _record(actor_key: str, row: Any) -> AuthorizationLease:
    return AuthorizationLease(
        actor_key=actor_key,
        lease_id=str(row[0]),
        thread_id=str(row[1]),
        mode=str(row[2]),
        allow_sensitive=bool(row[3]),
        created_at=row[4],
        expires_at=row[5],
        revoked_at=row[6],
    )


class PostgresAuthorizationLeaseRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def create(
        self,
        *,
        actor_key: str,
        thread_id: str,
        mode: AuthorizationMode,
        ttl_seconds: int,
        allow_sensitive: bool = False,
    ) -> AuthorizationLease:
        self._validate(actor_key, thread_id, mode, ttl_seconds, allow_sensitive)
        lease_id = new_runtime_id("authorization_lease")
        lock_key = f"{actor_key}:authorization-lease:{thread_id}"
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (lock_key,),
                )
                await connection.execute(
                    """
                    UPDATE nexus_domain.authorization_leases
                    SET revoked_at = now(), revocation_reason = 'replaced'
                    WHERE actor_key = %s AND thread_id = %s
                      AND revoked_at IS NULL
                    """,
                    (actor_key, thread_id),
                )
                cursor = await connection.execute(
                    """
                    INSERT INTO nexus_domain.authorization_leases
                        (actor_key, lease_id, thread_id, mode, allow_sensitive,
                         expires_at)
                    VALUES (%s, %s, %s, %s, %s,
                            now() + (%s * interval '1 second'))
                    RETURNING lease_id, thread_id, mode, allow_sensitive,
                              created_at, expires_at, revoked_at
                    """,
                    (
                        actor_key,
                        lease_id,
                        thread_id,
                        mode,
                        allow_sensitive,
                        ttl_seconds,
                    ),
                )
                return _record(actor_key, await cursor.fetchone())

    async def get_active(
        self,
        *,
        actor_key: str,
        thread_id: str,
    ) -> AuthorizationLease | None:
        self._validate_actor_thread(actor_key, thread_id)
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT lease_id, thread_id, mode, allow_sensitive,
                       created_at, expires_at, revoked_at
                FROM nexus_domain.authorization_leases
                WHERE actor_key = %s AND thread_id = %s
                  AND revoked_at IS NULL AND expires_at > now()
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (actor_key, thread_id),
            )
            row = await cursor.fetchone()
        return _record(actor_key, row) if row is not None else None

    async def revoke(
        self,
        *,
        actor_key: str,
        lease_id: str,
        reason: str = "user_revoked",
    ) -> bool:
        if not _ACTOR_KEY.fullmatch(actor_key):
            raise ValueError("Invalid actor key.")
        if not _LEASE_ID.fullmatch(lease_id):
            raise ValueError("Invalid authorization lease ID.")
        if reason not in _REVOCATION_REASONS:
            raise ValueError("Invalid authorization lease revocation reason.")
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE nexus_domain.authorization_leases
                SET revoked_at = now(), revocation_reason = %s
                WHERE actor_key = %s AND lease_id = %s
                  AND revoked_at IS NULL
                RETURNING lease_id
                """,
                (reason, actor_key, lease_id),
            )
            return await cursor.fetchone() is not None

    @staticmethod
    def _validate_actor_thread(actor_key: str, thread_id: str) -> None:
        if not _ACTOR_KEY.fullmatch(actor_key):
            raise ValueError("Invalid actor key.")
        if not _THREAD_ID.fullmatch(thread_id):
            raise ValueError("Invalid thread ID.")

    @classmethod
    def _validate(
        cls,
        actor_key: str,
        thread_id: str,
        mode: str,
        ttl_seconds: int,
        allow_sensitive: bool,
    ) -> None:
        cls._validate_actor_thread(actor_key, thread_id)
        if mode not in _MODES:
            raise ValueError("Invalid authorization mode.")
        if not 300 <= ttl_seconds <= 14_400:
            raise ValueError("Authorization lease TTL must be between 300 and 14400 seconds.")
        if mode != "full_access" and allow_sensitive:
            raise ValueError("Sensitive auto-approval requires full access mode.")


__all__ = ["PostgresAuthorizationLeaseRepository"]
