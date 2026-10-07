"""PostgreSQL durable workflow state, transition, event, and outbox adapter."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from source.contracts import validate_event_payload
from source.contracts.identity import new_runtime_id
from source.domain import WorkflowConflict, WorkflowRecord, WorkflowType


class PostgresWorkflowRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def start(
        self,
        *,
        actor_key: str,
        workflow_type: WorkflowType,
        idempotency_key: str,
        intent_digest: str,
        normalized_input: dict[str, Any],
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[WorkflowRecord, bool]:
        workflow_id = new_runtime_id("workflow")
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    INSERT INTO nexus_domain.workflows
                        (actor_key, workflow_id, workflow_type, idempotency_key,
                         intent_digest, schema_version, state, stage, normalized_input,
                         thread_id, run_id)
                    VALUES (%s, %s, %s, %s, %s, '1', 'requested',
                            'normalize', %s, %s, %s)
                    ON CONFLICT (actor_key, workflow_type, idempotency_key)
                    DO NOTHING
                    RETURNING actor_key, workflow_id, workflow_type, idempotency_key,
                              intent_digest, state, stage, normalized_input, output, error
                    """,
                    (
                        actor_key,
                        workflow_id,
                        workflow_type,
                        idempotency_key,
                        intent_digest,
                        Jsonb(normalized_input),
                        thread_id,
                        run_id,
                    ),
                )
                row = await cursor.fetchone()
                if row is not None:
                    record = _record(row)
                    await self._write_transition_event(
                        connection,
                        record,
                        sequence=1,
                        from_state=None,
                        to_state="requested",
                        stage="normalize",
                        metadata={},
                    )
                    return record, True
                cursor = await connection.execute(
                    """
                    SELECT actor_key, workflow_id, workflow_type, idempotency_key,
                           intent_digest, state, stage, normalized_input, output, error
                    FROM nexus_domain.workflows
                    WHERE actor_key = %s AND workflow_type = %s AND idempotency_key = %s
                    FOR UPDATE
                    """,
                    (actor_key, workflow_type, idempotency_key),
                )
                existing = await cursor.fetchone()
                if existing is None:
                    raise RuntimeError("Workflow disappeared during reservation.")
                record = _record(existing)
                if record.intent_digest != intent_digest:
                    raise WorkflowConflict(
                        "idempotency_conflict: workflow key has different normalized intent"
                    )
                return record, False

    async def transition(
        self,
        record: WorkflowRecord,
        *,
        allowed_from: set[str],
        to_state: str,
        stage: str,
        output_patch: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> WorkflowRecord:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT actor_key, workflow_id, workflow_type, idempotency_key,
                           intent_digest, state, stage, normalized_input, output, error
                    FROM nexus_domain.workflows
                    WHERE actor_key = %s AND workflow_id = %s
                    FOR UPDATE
                    """,
                    (record.actor_key, record.workflow_id),
                )
                current_row = await cursor.fetchone()
                if current_row is None:
                    raise RuntimeError("Unknown durable workflow.")
                current = _record(current_row)
                if current.state not in allowed_from:
                    if current.state == to_state:
                        return current
                    raise WorkflowConflict(
                        f"workflow_transition_conflict: {current.state} -> {to_state}"
                    )
                output = {**current.output, **(output_patch or {})}
                cursor = await connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM nexus_domain.workflow_transitions
                    WHERE actor_key = %s AND workflow_id = %s
                    """,
                    (record.actor_key, record.workflow_id),
                )
                sequence = int((await cursor.fetchone())[0])
                await connection.execute(
                    """
                    UPDATE nexus_domain.workflows
                    SET state = %s, stage = %s, output = %s, error = %s,
                        updated_at = now()
                    WHERE actor_key = %s AND workflow_id = %s
                    """,
                    (
                        to_state,
                        stage,
                        Jsonb(output),
                        Jsonb(error) if error is not None else None,
                        record.actor_key,
                        record.workflow_id,
                    ),
                )
                updated = WorkflowRecord(
                    **{
                        **current.__dict__,
                        "state": to_state,
                        "stage": stage,
                        "output": output,
                        "error": error,
                    }
                )
                await self._write_transition_event(
                    connection,
                    updated,
                    sequence=sequence,
                    from_state=current.state,
                    to_state=to_state,
                    stage=stage,
                    metadata={"error": error} if error else {},
                )
                return updated

    @staticmethod
    async def _write_transition_event(
        connection: Any,
        record: WorkflowRecord,
        *,
        sequence: int,
        from_state: str | None,
        to_state: str,
        stage: str,
        metadata: dict[str, Any],
    ) -> None:
        event_id = new_runtime_id("event")
        payload = {
            "workflow_id": record.workflow_id,
            "workflow_type": record.workflow_type,
            "from_state": from_state,
            "to_state": to_state,
            "stage": stage,
            **metadata,
        }
        payload = validate_event_payload("workflow.transitioned", payload)
        await connection.execute(
            """
            INSERT INTO nexus_domain.product_events
                (actor_key, event_id, schema_version, run_id, sequence,
                 event_type, sensitivity, payload)
            VALUES (%s, %s, 1, %s, %s, 'workflow.transitioned', 'internal', %s)
            """,
            (
                record.actor_key,
                event_id,
                record.workflow_id,
                sequence,
                Jsonb(payload),
            ),
        )
        await connection.execute(
            """
            INSERT INTO nexus_domain.workflow_transitions
                (actor_key, workflow_id, sequence, from_state, to_state,
                 stage, event_id, safe_metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.actor_key,
                record.workflow_id,
                sequence,
                from_state,
                to_state,
                stage,
                event_id,
                Jsonb(metadata),
            ),
        )
        await connection.execute(
            """
            INSERT INTO nexus_domain.transactional_outbox
                (actor_key, event_id, topic, payload)
            VALUES (%s, %s, 'product-events', %s)
            """,
            (record.actor_key, event_id, Jsonb(payload)),
        )


def _record(row: Any) -> WorkflowRecord:
    return WorkflowRecord(
        actor_key=row[0],
        workflow_id=row[1],
        workflow_type=row[2],
        idempotency_key=row[3],
        intent_digest=row[4],
        state=row[5],
        stage=row[6],
        normalized_input=row[7],
        output=row[8],
        error=row[9],
    )
