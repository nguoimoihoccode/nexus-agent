"""Actor-scoped PostgreSQL persistence for explicit user memory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from source.contracts.identity import content_hash
from source.domain import normalize_memory_key

MAX_USER_MEMORY_BYTES = 16 * 1024
MAX_USER_MEMORY_ITEMS = 128


@dataclass(frozen=True)
class UserMemory:
    actor_key: str
    content: str
    content_hash: str
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UserMemorySaveResult:
    memory: UserMemory
    changed: bool


def normalize_user_memory(content: str) -> tuple[str, str]:
    """Normalize and validate one bounded Markdown/text memory value."""
    if not isinstance(content, str):
        raise ValueError("User memory must be text.")
    normalized = content.strip()
    size = len(normalized.encode("utf-8"))
    if size == 0:
        raise ValueError("User memory cannot be empty.")
    if size > MAX_USER_MEMORY_BYTES:
        raise ValueError("User memory exceeds the 16 KiB limit.")
    return normalized, content_hash("user_memory", normalized)


def render_user_memory(items: Mapping[str, str]) -> str:
    """Render keyed facts into one deterministic prompt/API-compatible snapshot."""
    return "\n\n".join(
        f"[{key}]\n{items[key].strip()}" for key in sorted(items)
    )


class PostgresUserMemoryRepository:
    """Store keyed facts plus one materialized aggregate per actor."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def get(self, actor_key: str) -> UserMemory | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT actor_key, content, content_hash, revision,
                       created_at, updated_at
                FROM nexus_domain.user_memories
                WHERE actor_key = %s
                """,
                (actor_key,),
            )
            row = await cursor.fetchone()
        return _memory(row) if row is not None else None

    async def save(
        self,
        actor_key: str,
        content: str,
        *,
        memory_key: str,
    ) -> UserMemorySaveResult:
        normalized_key = normalize_memory_key(memory_key)
        normalized_item, item_digest = normalize_user_memory(content)
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{actor_key}:user-memory",),
                )
                cursor = await connection.execute(
                    """
                    SELECT memory_key, content
                    FROM nexus_domain.user_memory_items
                    WHERE actor_key = %s
                    """,
                    (actor_key,),
                )
                items = {str(row[0]): str(row[1]) for row in await cursor.fetchall()}
                if normalized_key not in items and len(items) >= MAX_USER_MEMORY_ITEMS:
                    raise ValueError("User memory exceeds the 128 item limit.")
                items[normalized_key] = normalized_item
                aggregate, aggregate_digest = normalize_user_memory(
                    render_user_memory(items)
                )

                cursor = await connection.execute(
                    """
                    INSERT INTO nexus_domain.user_memories
                        (actor_key, content, content_hash)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (actor_key) DO UPDATE
                    SET content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        revision = nexus_domain.user_memories.revision + 1,
                        updated_at = now()
                    WHERE nexus_domain.user_memories.content_hash
                          IS DISTINCT FROM EXCLUDED.content_hash
                    RETURNING actor_key, content, content_hash, revision,
                              created_at, updated_at
                    """,
                    (actor_key, aggregate, aggregate_digest),
                )
                row = await cursor.fetchone()
                changed = row is not None
                if row is None:
                    cursor = await connection.execute(
                        """
                        SELECT actor_key, content, content_hash, revision,
                               created_at, updated_at
                        FROM nexus_domain.user_memories
                        WHERE actor_key = %s
                        """,
                        (actor_key,),
                    )
                    row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError("User memory disappeared during save.")
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.user_memory_items
                        (actor_key, memory_key, content, content_hash)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (actor_key, memory_key) DO UPDATE
                    SET content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        revision = nexus_domain.user_memory_items.revision + 1,
                        updated_at = now()
                    WHERE nexus_domain.user_memory_items.content_hash
                          IS DISTINCT FROM EXCLUDED.content_hash
                    """,
                    (actor_key, normalized_key, normalized_item, item_digest),
                )
        return UserMemorySaveResult(memory=_memory(row), changed=changed)

    async def delete(self, actor_key: str) -> bool:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{actor_key}:user-memory",),
                )
                cursor = await connection.execute(
                    "DELETE FROM nexus_domain.user_memories WHERE actor_key = %s",
                    (actor_key,),
                )
        return cursor.rowcount > 0


def _memory(row: Any) -> UserMemory:
    return UserMemory(
        actor_key=row[0],
        content=row[1],
        content_hash=row[2],
        revision=int(row[3]),
        created_at=row[4],
        updated_at=row[5],
    )
