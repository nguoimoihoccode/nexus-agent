"""PostgreSQL approval request/decision ledger with atomic outbox events."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from psycopg.types.json import Jsonb

from source.contracts import validate_event_payload
from source.contracts.identity import new_runtime_id
from source.domain import ApprovalConflict, ApprovalRecord


class PostgresApprovalRepository:
    def __init__(self, pool: Any, *, approval_ttl_seconds: int = 900) -> None:
        if approval_ttl_seconds <= 0:
            raise ValueError("approval_ttl_seconds must be positive")
        self.pool = pool
        self.approval_ttl_seconds = approval_ttl_seconds

    async def request(
        self,
        *,
        actor_key: str,
        thread_id: str,
        run_id: str,
        action_digest: str,
        tool_name: str,
        target_boundary: str,
        normalized_arguments: dict[str, Any],
        agent_name: str = "supervisor",
        tool_call_id: str | None = None,
    ) -> tuple[ApprovalRecord, bool]:
        approval_id = new_runtime_id("approval_request")
        call_id = (tool_call_id or approval_id)[:256]
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    INSERT INTO nexus_domain.approval_requests
                        (actor_key, approval_request_id, thread_id, run_id,
                         action_digest, tool_name, target_boundary,
                         normalized_arguments, status, expires_at, agent_name,
                         tool_call_id, action_schema_version, human_summary,
                         sensitivity)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending',
                            now() + (%s * interval '1 second'), %s, %s, '1',
                            %s, 'restricted')
                    ON CONFLICT DO NOTHING
                    RETURNING approval_request_id, thread_id, run_id, action_digest,
                              tool_name, target_boundary, normalized_arguments, status,
                              tool_call_id
                    """,
                    (
                        actor_key,
                        approval_id,
                        thread_id,
                        run_id,
                        action_digest,
                        tool_name,
                        target_boundary,
                        Jsonb(normalized_arguments),
                        self.approval_ttl_seconds,
                        agent_name[:128],
                        call_id,
                        Jsonb(
                            _human_summary(
                                tool_name,
                                target_boundary,
                                normalized_arguments,
                            )
                        ),
                    ),
                )
                row = await cursor.fetchone()
                created = row is not None
                if row is None:
                    cursor = await connection.execute(
                        """
                        SELECT approval_request_id, thread_id, run_id, action_digest,
                               tool_name, target_boundary, normalized_arguments, status,
                               tool_call_id
                        FROM nexus_domain.approval_requests
                        WHERE actor_key = %s AND action_digest = %s
                          AND status <> 'expired'
                        FOR UPDATE
                        """,
                        (actor_key, action_digest),
                    )
                    row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError("Approval request disappeared.")
                # LangGraph resumes an interrupt in a new HTTP run. Preserve the
                # proposing run for audit, but only reuse it for the exact stable
                # tool call in the same thread.
                if (
                    row[1] != thread_id
                    or row[4] != tool_name
                    or row[5] != target_boundary
                    or row[6] != normalized_arguments
                    or (row[2] != run_id and row[8] != call_id)
                ):
                    raise ApprovalConflict("approval_action_digest_conflict")
                record = _record(actor_key, row)
                if created:
                    await self._event(
                        connection,
                        record,
                        "approval.requested",
                        {"status": "pending"},
                    )
                return record, created

    async def decide(
        self,
        record: ApprovalRecord,
        *,
        decision: Literal["approve", "reject"],
        idempotency_key: str | None = None,
        source: str = "native-human-in-the-loop",
    ) -> ApprovalRecord:
        status = "approved" if decision == "approve" else "rejected"
        expired = False
        decided: ApprovalRecord | None = None
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT approval_request_id, thread_id, run_id, action_digest,
                           tool_name, target_boundary, normalized_arguments, status,
                           expires_at <= now() AS is_expired
                    FROM nexus_domain.approval_requests
                    WHERE actor_key = %s AND approval_request_id = %s
                    FOR UPDATE
                    """,
                    (record.actor_key, record.approval_request_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise ApprovalConflict("approval_request_missing")
                current = _record(record.actor_key, row)
                if current.action_digest != record.action_digest:
                    raise ApprovalConflict("approval_action_digest_conflict")
                if (
                    current.thread_id != record.thread_id
                    or current.run_id != record.run_id
                    or current.tool_name != record.tool_name
                    or current.target_boundary != record.target_boundary
                    or current.normalized_arguments != record.normalized_arguments
                ):
                    raise ApprovalConflict("approval_record_context_conflict")
                if current.status == status:
                    return current
                if current.status == "expired":
                    expired = True
                elif row[8] and current.status == "pending":
                    cursor = await connection.execute(
                        """
                        UPDATE nexus_domain.approval_requests
                        SET status = 'expired', decided_at = now(),
                            decision_metadata = %s
                        WHERE actor_key = %s AND approval_request_id = %s
                        RETURNING approval_request_id, thread_id, run_id,
                                  action_digest, tool_name, target_boundary,
                                  normalized_arguments, status
                        """,
                        (
                            Jsonb({"reason": "approval_ttl_exceeded"}),
                            record.actor_key,
                            record.approval_request_id,
                        ),
                    )
                    decided = _record(record.actor_key, await cursor.fetchone())
                    await self._event(
                        connection,
                        decided,
                        "approval.expired",
                        {
                            "status": "expired",
                            "reason": "approval_ttl_exceeded",
                        },
                    )
                    expired = True
                if expired:
                    # Raise after the transaction commits the first expiry transition.
                    pass
                elif current.status != "pending":
                    raise ApprovalConflict("approval_decision_conflict")
                else:
                    cursor = await connection.execute(
                        """
                        UPDATE nexus_domain.approval_requests
                        SET status = %s, decided_at = now(), decision_metadata = %s,
                            decided_by_actor = %s
                        WHERE actor_key = %s AND approval_request_id = %s
                        RETURNING approval_request_id, thread_id, run_id,
                                  action_digest, tool_name, target_boundary,
                                  normalized_arguments, status
                        """,
                        (
                            status,
                            Jsonb({"decision": decision, "source": source}),
                            record.actor_key,
                            record.actor_key,
                            record.approval_request_id,
                        ),
                    )
                    decided = _record(record.actor_key, await cursor.fetchone())
                if (
                    not expired
                    and status == "rejected"
                    and decided.tool_name.startswith("publish_ai_trader_")
                ):
                    publication_type = (
                        "strategy"
                        if decided.tool_name.endswith("strategy")
                        else "discussion"
                    )
                    publication_key = idempotency_key or (
                        f"publication:{decided.action_digest[7:]}"
                    )
                    publication_id = new_runtime_id("publication")
                    await connection.execute(
                        """
                        INSERT INTO nexus_domain.publication_effects
                            (actor_key, publication_id, publication_type,
                             approval_request_id, action_digest,
                             idempotency_key, status, safe_error, thread_id,
                             run_id, evidence_references)
                        VALUES (%s, %s, %s, %s, %s, %s, 'rejected', %s,
                                %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            decided.actor_key,
                            publication_id,
                            publication_type,
                            decided.approval_request_id,
                            decided.action_digest,
                            publication_key,
                            Jsonb({"code": "approval_rejected"}),
                            decided.thread_id,
                            decided.run_id,
                            Jsonb(
                                sorted(
                                    {
                                        str(item)
                                        for item in (
                                            decided.normalized_arguments.get(
                                                "evidence_ids"
                                            )
                                            or []
                                        )
                                        if str(item)
                                    }
                                )[:50]
                            ),
                        ),
                    )
                    cursor = await connection.execute(
                        """
                        SELECT action_digest, status
                        FROM nexus_domain.publication_effects
                        WHERE actor_key = %s AND idempotency_key = %s
                        """,
                        (decided.actor_key, publication_key),
                    )
                    effect = await cursor.fetchone()
                    if effect is None or effect[0] != decided.action_digest:
                        raise ApprovalConflict("rejected_publication_key_conflict")
                if not expired:
                    decision_source = (
                        "authorization_lease"
                        if source.startswith("authorization-lease:")
                        else "human"
                    )
                    await self._event(
                        connection,
                        decided,
                        "approval.decided",
                        {
                            "decision": decision,
                            "status": status,
                            "decision_source": decision_source,
                        },
                    )
        if expired:
            raise ApprovalConflict("approval_request_expired")
        if decided is None:
            raise RuntimeError("Approval decision disappeared.")
        return decided

    async def record_execution(
        self,
        record: ApprovalRecord,
        *,
        status: Literal["executed", "failed"],
        reference: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Link one approved exact action to its terminal effect evidence."""
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT action_digest, status, execution_status,
                           execution_reference, execution_error
                    FROM nexus_domain.approval_requests
                    WHERE actor_key = %s AND approval_request_id = %s
                    FOR UPDATE
                    """,
                    (record.actor_key, record.approval_request_id),
                )
                current = await cursor.fetchone()
                if current is None:
                    raise ApprovalConflict("approval_request_missing")
                if current[0] != record.action_digest or current[1] != "approved":
                    raise ApprovalConflict("approval_action_not_approved")
                if current[2] == status:
                    if (current[3] or None) != reference or (current[4] or None) != error:
                        raise ApprovalConflict("approval_execution_conflict")
                    return
                if current[2] != "not_executed":
                    raise ApprovalConflict("approval_execution_conflict")
                await connection.execute(
                    """
                    UPDATE nexus_domain.approval_requests
                    SET execution_status = %s, executed_at = now(),
                        execution_reference = %s, execution_error = %s
                    WHERE actor_key = %s AND approval_request_id = %s
                    """,
                    (
                        status,
                        Jsonb(reference) if reference is not None else None,
                        Jsonb(error) if error is not None else None,
                        record.actor_key,
                        record.approval_request_id,
                    ),
                )

    async def require_approved(
        self,
        *,
        actor_key: str,
        action_digest: str,
        tool_name: str,
        target_boundary: str,
        normalized_arguments: dict[str, Any],
    ) -> ApprovalRecord:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT approval_request_id, thread_id, run_id, action_digest,
                       tool_name, target_boundary, normalized_arguments, status
                FROM nexus_domain.approval_requests
                WHERE actor_key = %s AND action_digest = %s
                  AND status = 'approved'
                """,
                (actor_key, action_digest),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ApprovalConflict("approval_request_missing")
        record = _record(actor_key, row)
        if (
            record.tool_name != tool_name
            or record.target_boundary != target_boundary
            or record.normalized_arguments != normalized_arguments
        ):
            raise ApprovalConflict("approval_action_digest_conflict")
        if record.status != "approved":
            raise ApprovalConflict("approval_action_not_approved")
        return record

    @staticmethod
    async def _event(
        connection: Any,
        record: ApprovalRecord,
        event_type: str,
        extra: dict[str, Any],
    ) -> None:
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{record.actor_key}:{record.run_id}",),
        )
        cursor = await connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
            FROM nexus_domain.product_events
            WHERE actor_key = %s AND run_id = %s
            """,
            (record.actor_key, record.run_id),
        )
        sequence = int((await cursor.fetchone())[0])
        event_id = new_runtime_id("event")
        payload = {
            "approval_request_id": record.approval_request_id,
            "action_digest": record.action_digest,
            "tool_name": record.tool_name,
            "target_boundary": record.target_boundary,
            **extra,
        }
        payload = validate_event_payload(event_type, payload)
        await connection.execute(
            """
            INSERT INTO nexus_domain.product_events
                (actor_key, event_id, schema_version, thread_id, run_id,
                 sequence, event_type, sensitivity, payload)
            VALUES (%s, %s, 1, %s, %s, %s, %s, 'restricted', %s)
            """,
            (
                record.actor_key,
                event_id,
                record.thread_id,
                record.run_id,
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
            (record.actor_key, event_id, Jsonb(payload)),
        )


def _record(actor_key: str, row: Any) -> ApprovalRecord:
    return ApprovalRecord(
        actor_key=actor_key,
        approval_request_id=row[0],
        thread_id=row[1],
        run_id=row[2],
        action_digest=row[3],
        tool_name=row[4],
        target_boundary=row[5],
        normalized_arguments=row[6],
        status=row[7],
    )


def _human_summary(
    tool_name: str,
    target_boundary: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in sorted(arguments.items()):
        if key in {"content", "new_value", "old_string", "new_string"}:
            encoded = str(value or "").encode("utf-8")
            fields[key] = {
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        elif isinstance(value, (str, int, float, bool)) or value is None:
            fields[key] = value if not isinstance(value, str) else value[:300]
        elif isinstance(value, list):
            fields[key] = [str(item)[:100] for item in value[:20]]
        else:
            fields[key] = {"type": type(value).__name__}
    return {
        "tool_name": tool_name,
        "target_boundary": target_boundary,
        "arguments": fields,
    }
