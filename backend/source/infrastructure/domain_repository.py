"""PostgreSQL adapter for the Nexus domain control-plane port."""

from __future__ import annotations

import re
from typing import Any

from psycopg.types.json import Jsonb

from source.contracts import validate_event_payload
from source.domain import IdempotencyConflict, IdempotencyDecision, ProductEvent
from source.contracts.identity import new_runtime_id

_ACTOR_PATTERN = re.compile(r"^v1-[0-9a-f]{64}$")
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,200}$")


class PostgresDomainControlRepository:
    """Store retry decisions and their terminal event/outbox atomically."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def reserve_idempotency(
        self,
        *,
        actor_key: str,
        operation: str,
        idempotency_key: str,
        arguments_digest: str,
    ) -> tuple[IdempotencyDecision, bool]:
        _validate_scope(actor_key, operation, idempotency_key, arguments_digest)
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    INSERT INTO nexus_domain.idempotency_decisions
                        (actor_key, operation, idempotency_key, arguments_digest, state)
                    VALUES (%s, %s, %s, %s, 'reserved')
                    ON CONFLICT (actor_key, operation, idempotency_key) DO NOTHING
                    RETURNING state, result_reference
                    """,
                    (actor_key, operation, idempotency_key, arguments_digest),
                )
                inserted = await cursor.fetchone()
                if inserted is not None:
                    return (
                        IdempotencyDecision(
                            actor_key=actor_key,
                            operation=operation,
                            idempotency_key=idempotency_key,
                            arguments_digest=arguments_digest,
                            state="reserved",
                        ),
                        True,
                    )
                cursor = await connection.execute(
                    """
                    SELECT arguments_digest, state, result_reference
                    FROM nexus_domain.idempotency_decisions
                    WHERE actor_key = %s AND operation = %s AND idempotency_key = %s
                    FOR UPDATE
                    """,
                    (actor_key, operation, idempotency_key),
                )
                existing = await cursor.fetchone()
                if existing is None:
                    raise RuntimeError("Idempotency decision disappeared during reservation.")
                if existing[0] != arguments_digest:
                    raise IdempotencyConflict(
                        "idempotency_conflict: key was reused with different arguments"
                    )
                return (
                    IdempotencyDecision(
                        actor_key=actor_key,
                        operation=operation,
                        idempotency_key=idempotency_key,
                        arguments_digest=arguments_digest,
                        state=existing[1],
                        result_reference=existing[2],
                    ),
                    False,
                )

    async def complete_idempotency_with_event(
        self,
        *,
        decision: IdempotencyDecision,
        result_reference: dict[str, Any],
        thread_id: str | None,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> ProductEvent:
        payload = validate_event_payload(event_type, payload)
        _validate_scope(
            decision.actor_key,
            decision.operation,
            decision.idempotency_key,
            decision.arguments_digest,
        )
        if not _TOKEN_PATTERN.fullmatch(run_id) or not _TOKEN_PATTERN.fullmatch(event_type):
            raise ValueError("Invalid run or event type.")
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT arguments_digest, state, result_reference
                    FROM nexus_domain.idempotency_decisions
                    WHERE actor_key = %s AND operation = %s AND idempotency_key = %s
                    FOR UPDATE
                    """,
                    (
                        decision.actor_key,
                        decision.operation,
                        decision.idempotency_key,
                    ),
                )
                current = await cursor.fetchone()
                if current is None or current[0] != decision.arguments_digest:
                    raise IdempotencyConflict(
                        "idempotency_conflict: decision cannot be completed"
                    )
                if current[1] == "completed":
                    event_id = (current[2] or {}).get("_completion_event_id")
                    if not event_id:
                        raise IdempotencyConflict(
                            "idempotency_conflict: completed decision lacks event evidence"
                        )
                    cursor = await connection.execute(
                        """
                        SELECT schema_version, thread_id, run_id, sequence, event_type,
                               sensitivity, payload
                        FROM nexus_domain.product_events
                        WHERE actor_key = %s AND event_id = %s
                        """,
                        (decision.actor_key, event_id),
                    )
                    existing_event = await cursor.fetchone()
                    if existing_event is None:
                        raise IdempotencyConflict(
                            "idempotency_conflict: completed event evidence is missing"
                        )
                    return ProductEvent(
                        actor_key=decision.actor_key,
                        event_id=event_id,
                        schema_version=existing_event[0],
                        thread_id=existing_event[1],
                        run_id=existing_event[2],
                        sequence=existing_event[3],
                        event_type=existing_event[4],
                        sensitivity=existing_event[5],
                        payload=existing_event[6],
                    )
                if current[1] != "reserved":
                    raise IdempotencyConflict(
                        "idempotency_conflict: decision is not reservable"
                    )
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{decision.actor_key}:{run_id}",),
                )
                cursor = await connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM nexus_domain.product_events
                    WHERE actor_key = %s AND run_id = %s
                    """,
                    (decision.actor_key, run_id),
                )
                sequence = int((await cursor.fetchone())[0])
                event_id = new_runtime_id("event")
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.product_events
                        (actor_key, event_id, schema_version, thread_id, run_id,
                         sequence, event_type, sensitivity, payload)
                    VALUES (%s, %s, 1, %s, %s, %s, %s, 'internal', %s)
                    """,
                    (
                        decision.actor_key,
                        event_id,
                        thread_id,
                        run_id,
                        sequence,
                        event_type,
                        Jsonb(payload),
                    ),
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.transactional_outbox
                        (actor_key, event_id, topic, payload)
                    VALUES (%s, %s, 'product-events', %s)
                    """,
                    (decision.actor_key, event_id, Jsonb(payload)),
                )
                await connection.execute(
                    """
                    UPDATE nexus_domain.idempotency_decisions
                    SET state = 'completed', result_reference = %s, updated_at = now()
                    WHERE actor_key = %s AND operation = %s AND idempotency_key = %s
                    """,
                    (
                        Jsonb(
                            {
                                **result_reference,
                                "_completion_event_id": event_id,
                            }
                        ),
                        decision.actor_key,
                        decision.operation,
                        decision.idempotency_key,
                    ),
                )
        return ProductEvent(
            actor_key=decision.actor_key,
            event_id=event_id,
            schema_version=1,
            thread_id=thread_id,
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            sensitivity="internal",
            payload=payload,
        )


def _validate_scope(
    actor_key: str,
    operation: str,
    idempotency_key: str,
    arguments_digest: str,
) -> None:
    if not _ACTOR_PATTERN.fullmatch(actor_key):
        raise ValueError("Invalid actor key.")
    if not _TOKEN_PATTERN.fullmatch(operation) or not _TOKEN_PATTERN.fullmatch(
        idempotency_key
    ):
        raise ValueError("Invalid operation or idempotency key.")
    if not re.fullmatch(r"(?:act_v1_|sha256:)[0-9a-f]{64}", arguments_digest):
        raise ValueError("Invalid normalized arguments digest.")
