"""Actor-scoped, idempotent model-assisted experiment interpretation evidence."""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg.types.json import Jsonb

from source.contracts.identity import content_hash, new_runtime_id


class InterpretationConflict(ValueError):
    """Interpretation idempotency or evidence references are invalid."""


class PostgresInterpretationRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def record(
        self,
        *,
        actor_key: str,
        experiment_id: str,
        structured_content: dict[str, Any],
        evidence_references: list[str],
        idempotency_key: str,
        thread_id: str | None,
        run_id: str | None,
    ) -> dict[str, Any]:
        digest = content_hash(
            "experiment_interpretation",
            {
                "experiment_id": experiment_id,
                "structured_content": structured_content,
                "evidence_references": sorted(evidence_references),
            },
        )
        report_id = new_runtime_id("interpretation_report")
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT 1 FROM nexus_domain.experiments
                    WHERE actor_key = %s AND experiment_id = %s
                    """,
                    (actor_key, experiment_id),
                )
                if await cursor.fetchone() is None:
                    raise InterpretationConflict("unknown_experiment")
                references = sorted(set(evidence_references) | {experiment_id})
                cursor = await connection.execute(
                    """
                    SELECT node_id FROM nexus_domain.lineage_nodes
                    WHERE actor_key = %s AND node_id = ANY(%s)
                    """,
                    (actor_key, references),
                )
                found = {row[0] for row in await cursor.fetchall()}
                missing = sorted(set(references) - found)
                if missing:
                    raise InterpretationConflict("unknown_evidence_reference")
                cursor = await connection.execute(
                    """
                    INSERT INTO nexus_domain.interpretation_reports
                        (actor_key, interpretation_report_id, experiment_id,
                         thread_id, run_id, schema_version, idempotency_key,
                         content_digest, structured_content, evidence_references,
                         mapping_origin)
                    VALUES (%s, %s, %s, %s, %s, '1', %s, %s, %s, %s,
                            'model_generated')
                    ON CONFLICT (actor_key, idempotency_key) DO NOTHING
                    RETURNING interpretation_report_id, created_at
                    """,
                    (
                        actor_key,
                        report_id,
                        experiment_id,
                        thread_id,
                        run_id,
                        idempotency_key,
                        digest,
                        Jsonb(structured_content),
                        Jsonb(sorted(set(evidence_references))),
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    cursor = await connection.execute(
                        """
                        SELECT interpretation_report_id, content_digest, created_at
                        FROM nexus_domain.interpretation_reports
                        WHERE actor_key = %s AND idempotency_key = %s
                        """,
                        (actor_key, idempotency_key),
                    )
                    existing = await cursor.fetchone()
                    if existing is None or existing[1] != digest:
                        raise InterpretationConflict("idempotency_conflict")
                    return {
                        "interpretation_report_id": existing[0],
                        "experiment_id": experiment_id,
                        "content_digest": digest,
                        "created_at": existing[2].isoformat(),
                        "reused": True,
                    }
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.lineage_nodes
                        (actor_key, node_id, node_type, schema_version, properties)
                    VALUES (%s, %s, 'interpretation_report', '1', %s)
                    ON CONFLICT (actor_key, node_id) DO NOTHING
                    """,
                    (
                        actor_key,
                        report_id,
                        Jsonb(
                            {
                                "interpretation_report_id": report_id,
                                "experiment_id": experiment_id,
                                "content_digest": digest,
                                "mapping_origin": "model_generated",
                                "evidence_references": sorted(
                                    set(evidence_references)
                                ),
                            }
                        ),
                    ),
                )
                for source_id in references:
                    await self._edge(
                        connection,
                        actor_key,
                        "interpreted_by" if source_id == experiment_id else "supports_interpretation",
                        source_id,
                        report_id,
                    )
        return {
            "interpretation_report_id": report_id,
            "experiment_id": experiment_id,
            "content_digest": digest,
            "created_at": row[1].isoformat(),
            "reused": False,
        }

    @staticmethod
    async def _edge(
        connection: Any,
        actor_key: str,
        edge_type: str,
        source_id: str,
        target_id: str,
    ) -> None:
        digest = hashlib.sha256(
            f"{actor_key}:{edge_type}:{source_id}:{target_id}".encode()
        ).hexdigest()
        await connection.execute(
            """
            INSERT INTO nexus_domain.lineage_edges
                (actor_key, edge_id, edge_type, source_node_id,
                 target_node_id, properties)
            VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)
            ON CONFLICT (actor_key, edge_type, source_node_id, target_node_id)
            DO NOTHING
            """,
            (actor_key, f"led_v1_{digest[:32]}", edge_type, source_id, target_id),
        )
