"""Compact event ledger, transactional-outbox projection, and replay reads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from source.contracts import (
    EVENT_SCHEMA_VERSION,
    ProductEventEnvelope,
    ReplayPage,
    validate_event_payload,
)
from source.contracts.identity import new_runtime_id


class PostgresProductEventRepository:
    def __init__(self, pool: Any, *, replay_retention_seconds: int = 86_400) -> None:
        self.pool = pool
        self.replay_retention_seconds = replay_retention_seconds

    async def append(
        self,
        *,
        actor_key: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        thread_id: str | None = None,
        sensitivity: str = "internal",
    ) -> dict[str, Any]:
        payload = validate_event_payload(event_type, payload)
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{actor_key}:{run_id}",),
                )
                cursor = await connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM nexus_domain.product_events
                    WHERE actor_key = %s AND run_id = %s
                    """,
                    (actor_key, run_id),
                )
                sequence = int((await cursor.fetchone())[0])
                event_id = new_runtime_id("event")
                occurred_at = datetime.now(UTC)
                envelope = ProductEventEnvelope.model_validate({
                    "event_id": event_id,
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "sequence": sequence,
                    "actor_key": actor_key,
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "event_type": event_type,
                    "occurred_at": occurred_at.isoformat(),
                    "sensitivity": sensitivity,
                    "payload": payload,
                }).model_dump(mode="json")
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.product_events
                        (actor_key, event_id, schema_version, thread_id, run_id,
                         sequence, event_type, sensitivity, payload, occurred_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        actor_key,
                        event_id,
                        EVENT_SCHEMA_VERSION,
                        thread_id,
                        run_id,
                        sequence,
                        event_type,
                        sensitivity,
                        Jsonb(payload),
                        occurred_at,
                    ),
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.transactional_outbox
                        (actor_key, event_id, topic, payload)
                    VALUES (%s, %s, 'product-events', %s)
                    """,
                    (actor_key, event_id, Jsonb(envelope)),
                )
        return envelope

    async def project_outbox(self, *, limit: int = 100) -> int:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT nexus_domain.project_product_outbox(%s, %s)",
                    (self.replay_retention_seconds, limit),
                )
                row = await cursor.fetchone()
        return int(row[0])

    async def fetch_after(
        self,
        *,
        actor_key: str,
        run_id: str,
        after: int,
        limit: int = 200,
    ) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT MIN(sequence), MAX(sequence)
                FROM nexus_domain.replay_events
                WHERE actor_key = %s AND run_id = %s AND expires_at > now()
                """,
                (actor_key, run_id),
            )
            bounds = await cursor.fetchone()
            cursor = await connection.execute(
                """
                SELECT MAX(sequence)
                FROM nexus_domain.product_events
                WHERE actor_key = %s AND run_id = %s
                """,
                (actor_key, run_id),
            )
            durable_max = int((await cursor.fetchone())[0] or 0)
            cursor = await connection.execute(
                """
                SELECT payload
                FROM nexus_domain.replay_events
                WHERE actor_key = %s AND run_id = %s
                  AND sequence > %s AND expires_at > now()
                ORDER BY sequence
                LIMIT %s
                """,
                (actor_key, run_id, after, limit),
            )
            events = [row[0] for row in await cursor.fetchall()]
        retained_from = int(bounds[0]) if bounds and bounds[0] is not None else None
        last_sequence = events[-1]["sequence"] if events else after
        return ReplayPage.model_validate(
            {
                "events": events,
                "last_sequence": last_sequence,
                "gap": durable_max > after
                and (retained_from is None or after + 1 < retained_from),
                "retained_from_sequence": retained_from,
            }
        ).model_dump(mode="json")
