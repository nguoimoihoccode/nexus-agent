"""Shared PostgreSQL connection pool."""

import re

from psycopg import AsyncConnection
from psycopg.rows import tuple_row
from psycopg_pool import AsyncConnectionPool

from source.core.config import settings
from source.security import PermissionDenied, current_actor_key

_DATABASE_ACTOR = re.compile(r"^v1-[0-9a-f]{64}$")


async def _bind_database_actor(connection: AsyncConnection) -> None:
    try:
        actor_key = current_actor_key()
    except PermissionDenied:
        actor_key = ""
    if not _DATABASE_ACTOR.fullmatch(actor_key):
        actor_key = ""
    await connection.execute(
        "SELECT set_config('nexus.actor_key', %s, false)", (actor_key,)
    )


async def _reset_database_actor(connection: AsyncConnection) -> None:
    await connection.execute("RESET nexus.actor_key")


def create_db_pool() -> AsyncConnectionPool:
    """Create the application pool without opening it eagerly."""
    return AsyncConnectionPool(
        conninfo=settings.postgres_url,
        min_size=settings.postgres_pool_min_size,
        max_size=settings.postgres_pool_max_size,
        kwargs={
            "autocommit": True,
            "options": "-csearch_path=langgraph,public",
            "prepare_threshold": 0,
            # Repositories use positional access for stable SELECT contracts.
            # Keep the pool's row shape aligned with those contracts; dict rows
            # make otherwise valid reads fail with ``KeyError: 0``.
            "row_factory": tuple_row,
        },
        open=False,
        name="nexus-agent",
        check=_bind_database_actor,
        reset=_reset_database_actor,
    )
