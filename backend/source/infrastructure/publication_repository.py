"""PostgreSQL approval and publication-effect repository."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from source.contracts import validate_event_payload
from source.contracts.identity import new_runtime_id
from source.domain import PublicationConflict, PublicationEffect


class PostgresPublicationRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def reserve_approved_effect(
        self,
        *,
        actor_key: str,
        publication_type: str,
        tool_name: str,
        target_boundary: str,
        normalized_arguments: dict[str, Any],
        action_digest: str,
        idempotency_key: str,
        thread_id: str,
        run_id: str,
    ) -> tuple[PublicationEffect, bool]:
        evidence_references = sorted(
            {
                str(item)
                for item in (normalized_arguments.get("evidence_ids") or [])
                if str(item)
            }
        )[:50]
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT approval_request_id, status, tool_name, target_boundary,
                           normalized_arguments
                    FROM nexus_domain.approval_requests
                    WHERE actor_key = %s AND action_digest = %s
                      AND status = 'approved'
                    FOR UPDATE
                    """,
                    (actor_key, action_digest),
                )
                approval = await cursor.fetchone()
                if approval is None or approval[1] != "approved":
                    raise PublicationConflict("publication_action_not_approved")
                if (
                    approval[2] != tool_name
                    or approval[3] != target_boundary
                    or approval[4] != normalized_arguments
                ):
                    raise PublicationConflict("publication_approval_digest_conflict")

                publication_id = new_runtime_id("publication")
                cursor = await connection.execute(
                    """
                    INSERT INTO nexus_domain.publication_effects
                        (actor_key, publication_id, publication_type,
                         approval_request_id, action_digest, idempotency_key,
                         status, thread_id, run_id, evidence_references)
                    VALUES (%s, %s, %s, %s, %s, %s, 'reserved', %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING actor_key, publication_id, publication_type,
                              approval_request_id, action_digest, idempotency_key,
                              status, target_reference, safe_error
                    """,
                    (
                        actor_key,
                        publication_id,
                        publication_type,
                        approval[0],
                        action_digest,
                        idempotency_key,
                        thread_id,
                        run_id,
                        Jsonb(evidence_references),
                    ),
                )
                row = await cursor.fetchone()
                created = row is not None
                if row is None:
                    cursor = await connection.execute(
                        """
                        SELECT actor_key, publication_id, publication_type,
                               approval_request_id, action_digest, idempotency_key,
                               status, target_reference, safe_error
                        FROM nexus_domain.publication_effects
                        WHERE actor_key = %s
                          AND (idempotency_key = %s OR action_digest = %s)
                        ORDER BY (idempotency_key = %s) DESC
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (
                            actor_key,
                            idempotency_key,
                            action_digest,
                            idempotency_key,
                        ),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise RuntimeError("Publication reservation disappeared.")
                    if row[2] != publication_type or row[4] != action_digest:
                        raise PublicationConflict("publication_idempotency_conflict")
                    if row[6] == "rejected":
                        raise PublicationConflict("publication_action_rejected")
                    if row[6] == "failed":
                        cursor = await connection.execute(
                            """
                            UPDATE nexus_domain.publication_effects
                            SET status = 'reserved', safe_error = NULL, updated_at = now()
                            WHERE actor_key = %s AND publication_id = %s
                            RETURNING actor_key, publication_id, publication_type,
                                      approval_request_id, action_digest,
                                      idempotency_key, status, target_reference,
                                      safe_error
                            """,
                            (actor_key, row[1]),
                        )
                        row = await cursor.fetchone()
                        await connection.execute(
                            """
                            UPDATE nexus_domain.approval_requests
                            SET execution_status = 'not_executed', executed_at = NULL,
                                execution_reference = NULL, execution_error = NULL
                            WHERE actor_key = %s AND approval_request_id = %s
                              AND execution_status = 'failed'
                            """,
                            (actor_key, row[3]),
                        )
                return _effect(row), created

    async def mark_published(
        self,
        effect: PublicationEffect,
        *,
        thread_id: str,
        run_id: str,
        target_reference: dict[str, Any],
    ) -> PublicationEffect:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT status, target_reference
                    FROM nexus_domain.publication_effects
                    WHERE actor_key = %s AND publication_id = %s
                    FOR UPDATE
                    """,
                    (effect.actor_key, effect.publication_id),
                )
                current = await cursor.fetchone()
                if current is None:
                    raise RuntimeError("Publication effect disappeared.")
                if current[0] == "published":
                    return PublicationEffect(
                        **{
                            **effect.__dict__,
                            "status": "published",
                            "target_reference": current[1],
                            "safe_error": None,
                        }
                    )
                if current[0] != "reserved":
                    raise PublicationConflict("publication_transition_conflict")

                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{effect.actor_key}:{run_id}",),
                )
                cursor = await connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM nexus_domain.product_events
                    WHERE actor_key = %s AND run_id = %s
                    """,
                    (effect.actor_key, run_id),
                )
                sequence = int((await cursor.fetchone())[0])
                event_id = new_runtime_id("event")
                payload = {
                    "publication_id": effect.publication_id,
                    "publication_type": effect.publication_type,
                    "approval_request_id": effect.approval_request_id,
                    "action_digest": effect.action_digest,
                    "target_reference": target_reference,
                }
                payload = validate_event_payload("publication.published", payload)
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.product_events
                        (actor_key, event_id, schema_version, thread_id, run_id,
                         sequence, event_type, sensitivity, payload)
                    VALUES (%s, %s, 1, %s, %s, %s,
                            'publication.published', 'internal', %s)
                    """,
                    (
                        effect.actor_key,
                        event_id,
                        thread_id,
                        run_id,
                        sequence,
                        Jsonb(payload),
                    ),
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.transactional_outbox
                        (actor_key, event_id, topic, payload)
                    VALUES (%s, %s, 'product-events', %s)
                    """,
                    (effect.actor_key, event_id, Jsonb(payload)),
                )
                cursor = await connection.execute(
                    """
                    UPDATE nexus_domain.publication_effects
                    SET status = 'published', target_reference = %s,
                        safe_error = NULL, updated_at = now()
                    WHERE actor_key = %s AND publication_id = %s
                    RETURNING actor_key, publication_id, publication_type,
                              approval_request_id, action_digest, idempotency_key,
                              status, target_reference, safe_error
                    """,
                    (Jsonb(target_reference), effect.actor_key, effect.publication_id),
                )
                published = _effect(await cursor.fetchone())
                await connection.execute(
                    """
                    UPDATE nexus_domain.approval_requests
                    SET execution_status = 'executed', executed_at = now(),
                        execution_reference = %s, execution_error = NULL
                    WHERE actor_key = %s AND approval_request_id = %s
                      AND status = 'approved'
                      AND execution_status IN ('not_executed', 'failed', 'executed')
                    """,
                    (
                        Jsonb(
                            {
                                "effect_type": "publication",
                                "publication_id": effect.publication_id,
                                "target_reference": target_reference,
                            }
                        ),
                        effect.actor_key,
                        effect.approval_request_id,
                    ),
                )
                return published

    async def mark_failed(
        self,
        effect: PublicationEffect,
        *,
        safe_error: dict[str, Any],
    ) -> PublicationEffect:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE nexus_domain.publication_effects
                    SET status = 'failed', safe_error = %s, updated_at = now()
                    WHERE actor_key = %s AND publication_id = %s
                      AND status = 'reserved'
                    RETURNING actor_key, publication_id, publication_type,
                              approval_request_id, action_digest, idempotency_key,
                              status, target_reference, safe_error
                    """,
                    (Jsonb(safe_error), effect.actor_key, effect.publication_id),
                )
                row = await cursor.fetchone()
                if row is not None:
                    await connection.execute(
                        """
                        UPDATE nexus_domain.approval_requests
                        SET execution_status = 'failed', executed_at = now(),
                            execution_reference = %s, execution_error = %s
                        WHERE actor_key = %s AND approval_request_id = %s
                          AND status = 'approved'
                          AND execution_status <> 'executed'
                        """,
                        (
                            Jsonb(
                                {
                                    "effect_type": "publication",
                                    "publication_id": effect.publication_id,
                                }
                            ),
                            Jsonb(safe_error),
                            effect.actor_key,
                            effect.approval_request_id,
                        ),
                    )
        return _effect(row) if row is not None else effect


def _effect(row: Any) -> PublicationEffect:
    return PublicationEffect(
        actor_key=row[0],
        publication_id=row[1],
        publication_type=row[2],
        approval_request_id=row[3],
        action_digest=row[4],
        idempotency_key=row[5],
        status=row[6],
        target_reference=row[7],
        safe_error=row[8],
    )
