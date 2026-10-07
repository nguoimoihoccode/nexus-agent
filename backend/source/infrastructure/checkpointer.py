"""Postgres checkpointer."""

import os

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg_pool import AsyncConnectionPool


def checkpoint_serializer() -> SerializerProtocol:
    """Use a strict serializer and encrypt checkpoint bytes when configured."""
    safe_serializer = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=[],
        allowed_msgpack_modules=None,
    )
    key = os.environ.get("LANGGRAPH_AES_KEY", "").encode()
    if key:
        return EncryptedSerializer.from_pycryptodome_aes(
            safe_serializer,
            key=key,
        )
    if os.environ.get("NEXUS_ENV", "development") == "production":
        raise RuntimeError("LANGGRAPH_AES_KEY is required for production checkpoints.")
    return safe_serializer


async def initialize_checkpointer(
    pool: AsyncConnectionPool,
) -> AsyncPostgresSaver:
    """Create and set up a reusable PostgreSQL checkpointer."""
    checkpointer = AsyncPostgresSaver(pool, serde=checkpoint_serializer())
    await checkpointer.setup()
    return checkpointer
