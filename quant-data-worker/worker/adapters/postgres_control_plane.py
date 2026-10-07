"""PostgreSQL metadata adapter for immutable quant-data revisions."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from worker.domain import WorkerError, action_fingerprint
from worker.models import (
    BuildQlibDatasetRequest,
    FactorSnapshotRequest,
    FactorSnapshotResponse,
    FetchOhlcvRequest,
    FetchOhlcvResponse,
    QlibDatasetBuildResponse,
)
from worker.ports import QuantDataControlPlaneError
from worker.security import (
    bind_database_actor,
    current_actor_key,
    reset_database_actor,
)

DOMAIN_SCHEMA_VERSION = 14


class QuantDataPersistenceError(QuantDataControlPlaneError):
    """A safe control-plane persistence failure."""


class PostgresQuantDataControlPlane:
    def __init__(self, conninfo: str) -> None:
        self.pool = ConnectionPool(
            conninfo,
            min_size=1,
            max_size=5,
            kwargs={"prepare_threshold": 0},
            name="quant-data-control-plane",
            open=True,
            check=bind_database_actor,
            reset=reset_database_actor,
        )
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT version FROM nexus_domain.schema_migrations "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
        if row is None or row[0] != DOMAIN_SCHEMA_VERSION:
            self.pool.close()
            raise QuantDataPersistenceError(
                "Domain schema version is incompatible with quant-data-worker."
            )
        self._active_ingestion: ContextVar[tuple[Any, str, str, str] | None] = (
            ContextVar("quant_data_ingestion", default=None)
        )

    def readiness(self) -> bool:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT version FROM nexus_domain.schema_migrations "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
        return bool(row and row[0] == DOMAIN_SCHEMA_VERSION)

    @contextmanager
    def ingestion_guard(self, operation: str, request: Any, response_type: type[Any]):
        key = getattr(request, "idempotency_key", None)
        if not key:
            yield None
            return
        actor = current_actor_key()
        normalized = request.model_dump(mode="json", exclude={"idempotency_key"})
        digest = action_fingerprint(
            {
                "actor_key": actor,
                "arguments": normalized,
                "operation": operation,
                "schema_version": "1",
                "target_boundary": "quant-data-worker",
            }
        )
        try:
            with self.pool.connection() as connection, connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{actor}:{operation}:{key}",),
                )
                connection.execute(
                    """
                    INSERT INTO nexus_domain.idempotency_decisions
                        (actor_key, operation, idempotency_key,
                         arguments_digest, state)
                    VALUES (%s, %s, %s, %s, 'reserved')
                    ON CONFLICT (actor_key, operation, idempotency_key) DO NOTHING
                    """,
                    (actor, operation, key, digest),
                )
                row = connection.execute(
                    """
                    SELECT arguments_digest, state, result_reference
                    FROM nexus_domain.idempotency_decisions
                    WHERE actor_key = %s AND operation = %s AND idempotency_key = %s
                    FOR UPDATE
                    """,
                    (actor, operation, key),
                ).fetchone()
                if row is None:
                    raise QuantDataPersistenceError(
                        "Provider-ingestion reservation disappeared."
                    )
                if row[0] != digest:
                    raise QuantDataPersistenceError(
                        "idempotency_conflict: provider arguments changed"
                    )
                if row[1] == "completed":
                    yield response_type.model_validate(row[2])
                    return
                token = self._active_ingestion.set(
                    (connection, operation, key, digest)
                )
                try:
                    yield None
                finally:
                    self._active_ingestion.reset(token)
        except (QuantDataPersistenceError, WorkerError):
            raise
        except Exception as exc:
            raise QuantDataPersistenceError(
                "Provider-ingestion decision could not be committed."
            ) from exc

    def record_staging(
        self,
        request: FetchOhlcvRequest,
        response: FetchOhlcvResponse,
    ) -> None:
        actor = current_actor_key()
        try:
            active = self._active_ingestion.get()
            if active is not None:
                self._record_staging(active[0], actor, request, response)
                self._complete_ingestion(
                    active,
                    response.model_dump(mode="json", by_alias=True),
                )
            else:
                with self.pool.connection() as connection, connection.transaction():
                    self._record_staging(connection, actor, request, response)
        except Exception as exc:
            raise QuantDataPersistenceError(
                "Quant-data metadata could not be committed."
            ) from exc

    def _record_staging(self, connection, actor, request, response) -> None:
        normalized_request = request.model_dump(
            mode="json",
            exclude={"idempotency_key"},
        )
        connection.execute(
            """
            INSERT INTO nexus_domain.request_fingerprints
                (actor_key, request_fingerprint, namespace, schema_version,
                 normalized_request)
            VALUES (%s, %s, 'openbb_ohlcv', %s, %s)
            ON CONFLICT (actor_key, request_fingerprint) DO NOTHING
            """,
            (
                actor,
                response.request_fingerprint,
                response.schema_version,
                Jsonb(normalized_request),
            ),
        )
        connection.execute(
            """
            INSERT INTO nexus_domain.staging_revisions
                (actor_key, staging_revision_id, request_fingerprint,
                 content_hash, schema_version, metadata, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'ready')
            ON CONFLICT (actor_key, staging_revision_id) DO NOTHING
            """,
            (
                actor,
                response.staging_revision_id,
                response.request_fingerprint,
                response.content_hash,
                response.schema_version,
                Jsonb(response.model_dump(mode="json", by_alias=True)),
            ),
        )
        self._lineage_node(
            connection,
            actor,
            response.request_fingerprint,
            "provider_request",
            response.schema_version,
            {"namespace": "openbb_ohlcv", "request": normalized_request},
        )
        self._lineage_node(
            connection,
            actor,
            response.staging_revision_id,
            "staging_revision",
            response.schema_version,
            response.model_dump(mode="json", by_alias=True),
        )
        self._lineage_edge(
            connection,
            actor,
            "produced_revision",
            response.request_fingerprint,
            response.staging_revision_id,
        )
        self._event(
            connection,
            actor,
            response.request_fingerprint,
            "staging.ready",
            {
                "staging_revision_id": response.staging_revision_id,
                "content_hash": response.content_hash,
            },
        )

    def record_factor_snapshot(
        self,
        request: FactorSnapshotRequest,
        response: FactorSnapshotResponse,
    ) -> None:
        actor = current_actor_key()
        try:
            active = self._active_ingestion.get()
            if active is not None:
                self._record_factor(active[0], actor, request, response)
                self._complete_ingestion(
                    active,
                    response.model_dump(mode="json", by_alias=True),
                )
            else:
                with self.pool.connection() as connection, connection.transaction():
                    self._record_factor(connection, actor, request, response)
        except Exception as exc:
            raise QuantDataPersistenceError(
                "Factor metadata could not be committed."
            ) from exc

    def _record_factor(self, connection, actor, request, response) -> None:
        normalized_request = request.model_dump(
            mode="json",
            exclude={"idempotency_key"},
        )
        connection.execute(
            """
            INSERT INTO nexus_domain.request_fingerprints
                (actor_key, request_fingerprint, namespace, schema_version,
                 normalized_request)
            VALUES (%s, %s, 'factor_snapshot', %s, %s)
            ON CONFLICT (actor_key, request_fingerprint) DO NOTHING
            """,
            (
                actor,
                response.request_fingerprint,
                response.schema_version,
                Jsonb(normalized_request),
            ),
        )
        self._lineage_node(
            connection,
            actor,
            response.request_fingerprint,
            "provider_request",
            response.schema_version,
            {"namespace": "factor_snapshot", "request": normalized_request},
        )
        self._lineage_node(
            connection,
            actor,
            response.factor_snapshot_revision_id,
            "factor_snapshot",
            response.schema_version,
            response.model_dump(mode="json"),
        )
        self._lineage_edge(
            connection,
            actor,
            "produced_revision",
            response.request_fingerprint,
            response.factor_snapshot_revision_id,
        )
        self._event(
            connection,
            actor,
            response.request_fingerprint,
            "factor_snapshot.ready",
            {"factor_snapshot_revision_id": response.factor_snapshot_revision_id},
        )

    @staticmethod
    def _complete_ingestion(active, result: dict[str, Any]) -> None:
        connection, operation, key, digest = active
        cursor = connection.execute(
            """
            UPDATE nexus_domain.idempotency_decisions
            SET state = 'completed', result_reference = %s, updated_at = now()
            WHERE actor_key = %s AND operation = %s AND idempotency_key = %s
              AND arguments_digest = %s AND state = 'reserved'
            """,
            (
                Jsonb(result),
                current_actor_key(),
                operation,
                key,
                digest,
            ),
        )
        if cursor.rowcount != 1:
            raise QuantDataPersistenceError(
                "Provider-ingestion completion conflicted with its reservation."
            )

    def record_dataset(
        self,
        request: BuildQlibDatasetRequest,
        response: QlibDatasetBuildResponse,
        manifest: dict[str, Any],
    ) -> None:
        actor = current_actor_key()
        try:
            with self.pool.connection() as connection, connection.transaction():
                limitations = {
                    **response.limitations.model_dump(mode="json"),
                    "warnings": response.warnings,
                }
                connection.execute(
                    """
                    INSERT INTO nexus_domain.dataset_revisions
                        (actor_key, dataset_revision_id, source_staging_revision_id,
                         manifest_hash, schema_version, status, limitations, ready_at)
                    VALUES (%s, %s, %s, %s, %s, 'ready', %s, now())
                    ON CONFLICT (actor_key, dataset_revision_id) DO NOTHING
                    """,
                    (
                        actor,
                        response.dataset_revision_id,
                        response.source_staging_revision_id,
                        response.manifest_hash,
                        response.schema_version,
                        Jsonb(limitations),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO nexus_domain.dataset_aliases
                        (actor_key, dataset_alias, dataset_revision_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (actor_key, dataset_alias) DO UPDATE
                    SET dataset_revision_id = EXCLUDED.dataset_revision_id
                    WHERE nexus_domain.dataset_aliases.dataset_revision_id =
                          EXCLUDED.dataset_revision_id
                    """,
                    (actor, response.dataset_alias, response.dataset_revision_id),
                )
                for item in manifest["files"]:
                    connection.execute(
                        """
                        INSERT INTO nexus_domain.dataset_manifest_files
                            (actor_key, dataset_revision_id, relative_path,
                             media_type, size_bytes, content_hash)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (actor_key, dataset_revision_id, relative_path)
                        DO NOTHING
                        """,
                        (
                            actor,
                            response.dataset_revision_id,
                            item["path"],
                            item["media_type"],
                            item["size"],
                            f"sha256:{item['sha256']}",
                        ),
                    )
                self._lineage_node(
                    connection,
                    actor,
                    response.source_staging_revision_id,
                    "staging_revision",
                    response.schema_version,
                    {"staging_revision_id": response.source_staging_revision_id},
                )
                self._lineage_node(
                    connection,
                    actor,
                    response.dataset_revision_id,
                    "dataset_revision",
                    response.schema_version,
                    {
                        **response.model_dump(mode="json"),
                        "manifest": manifest,
                    },
                )
                self._lineage_edge(
                    connection,
                    actor,
                    "transformed_into",
                    response.source_staging_revision_id,
                    response.dataset_revision_id,
                )
                self._event(
                    connection,
                    actor,
                    response.dataset_revision_id,
                    "dataset.ready",
                    {
                        "dataset_revision_id": response.dataset_revision_id,
                        "manifest_hash": response.manifest_hash,
                        "dataset_alias": str(request.dataset_alias),
                    },
                )
        except Exception as exc:
            raise QuantDataPersistenceError(
                "Dataset metadata could not be committed."
            ) from exc

    def record_validation(
        self,
        entity_id: str,
        entity_type: str,
        validation: dict[str, Any],
    ) -> None:
        if entity_type not in {"staging_revision", "dataset_revision"}:
            raise QuantDataPersistenceError("Unsupported validation entity type.")
        actor = current_actor_key()
        digest = hashlib.sha256(
            (
                f"{actor}:{entity_type}:{entity_id}:"
                + json.dumps(
                    validation,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ).encode()
        ).hexdigest()
        validation_id = f"val_v1_{digest[:32]}"
        try:
            with self.pool.connection() as connection, connection.transaction():
                existing = connection.execute(
                    """
                    SELECT 1 FROM nexus_domain.lineage_nodes
                    WHERE actor_key = %s AND node_id = %s AND node_type = %s
                    """,
                    (actor, entity_id, entity_type),
                ).fetchone()
                if existing is None:
                    raise QuantDataPersistenceError(
                        "Validation target is not registered for this actor."
                    )
                self._lineage_node(
                    connection,
                    actor,
                    validation_id,
                    "validation_result",
                    "1",
                    {
                        "entity_id": entity_id,
                        "entity_type": entity_type,
                        "validation": validation,
                    },
                )
                self._lineage_edge(
                    connection,
                    actor,
                    "validated_by",
                    entity_id,
                    validation_id,
                )
        except QuantDataPersistenceError:
            raise
        except Exception as exc:
            raise QuantDataPersistenceError(
                "Validation evidence could not be committed."
            ) from exc

    @staticmethod
    def _lineage_node(
        connection,
        actor: str,
        node_id: str,
        node_type: str,
        schema_version: str,
        properties: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO nexus_domain.lineage_nodes
                (actor_key, node_id, node_type, schema_version, properties)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (actor_key, node_id) DO NOTHING
            """,
            (actor, node_id, node_type, schema_version, Jsonb(properties)),
        )

    @staticmethod
    def _lineage_edge(
        connection,
        actor: str,
        edge_type: str,
        source_node_id: str,
        target_node_id: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        digest = hashlib.sha256(
            f"{actor}:{edge_type}:{source_node_id}:{target_node_id}".encode()
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO nexus_domain.lineage_edges
                (actor_key, edge_id, edge_type, source_node_id,
                 target_node_id, properties)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (actor_key, edge_type, source_node_id, target_node_id)
            DO NOTHING
            """,
            (
                actor,
                f"led_v1_{digest[:32]}",
                edge_type,
                source_node_id,
                target_node_id,
                Jsonb(properties or {}),
            ),
        )

    @staticmethod
    def _event(connection, actor: str, run_id: str, event_type: str, payload: dict) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{actor}:{run_id}",),
        )
        sequence = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
            FROM nexus_domain.product_events
            WHERE actor_key = %s AND run_id = %s
            """,
            (actor, run_id),
        ).fetchone()[0]
        event_id = f"evt_v1_{uuid4().hex}"
        connection.execute(
            """
            INSERT INTO nexus_domain.product_events
                (actor_key, event_id, schema_version, run_id, sequence,
                 event_type, sensitivity, payload)
            VALUES (%s, %s, 1, %s, %s, %s, 'internal', %s)
            """,
            (actor, event_id, run_id, sequence, event_type, Jsonb(payload)),
        )
        connection.execute(
            """
            INSERT INTO nexus_domain.transactional_outbox
                (actor_key, event_id, topic, payload)
            VALUES (%s, %s, 'product-events', %s)
            """,
            (actor, event_id, Jsonb(payload)),
        )

    def close(self) -> None:
        self.pool.close()
