"""Actor-scoped lineage graph and experiment dossier reads."""

from __future__ import annotations

from typing import Any, Literal

from psycopg.types.json import Jsonb

from source.contracts.identity import content_hash, new_runtime_id


class PostgresLineageRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def graph(
        self,
        *,
        actor_key: str,
        node_id: str,
        direction: Literal["backward", "forward", "both"] = "both",
        depth: int = 4,
        limit: int = 500,
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT node_id, node_type, schema_version, properties, created_at
                FROM nexus_domain.lineage_nodes
                WHERE actor_key = %s AND node_id = %s
                """,
                (actor_key, node_id),
            )
            root = await cursor.fetchone()
            if root is None:
                return None
            seen = {node_id}
            frontier = {node_id}
            edges: dict[str, tuple[Any, ...]] = {}
            truncated = False
            for _level in range(depth):
                if not frontier or len(seen) >= limit or len(edges) >= limit:
                    truncated = bool(frontier)
                    break
                frontier_ids = list(frontier)
                cursor = await connection.execute(
                    """
                    SELECT edge_id, edge_type, source_node_id, target_node_id,
                           properties, created_at
                    FROM nexus_domain.lineage_edges
                    WHERE actor_key = %s
                      AND (
                        (%s AND source_node_id = ANY(%s))
                        OR (%s AND target_node_id = ANY(%s))
                      )
                    ORDER BY created_at, edge_id
                    LIMIT %s
                    """,
                    (
                        actor_key,
                        direction in {"forward", "both"},
                        frontier_ids,
                        direction in {"backward", "both"},
                        frontier_ids,
                        max(1, limit - len(edges)),
                    ),
                )
                rows = await cursor.fetchall()
                next_frontier: set[str] = set()
                for row in rows:
                    edges[row[0]] = row
                    for candidate in (row[2], row[3]):
                        if candidate not in seen:
                            next_frontier.add(candidate)
                available = max(0, limit - len(seen))
                if len(next_frontier) > available:
                    truncated = True
                    next_frontier = set(sorted(next_frontier)[:available])
                seen.update(next_frontier)
                frontier = next_frontier
            cursor = await connection.execute(
                """
                SELECT node_id, node_type, schema_version, properties, created_at
                FROM nexus_domain.lineage_nodes
                WHERE actor_key = %s AND node_id = ANY(%s)
                ORDER BY created_at, node_id
                """,
                (actor_key, list(seen)),
            )
            nodes = await cursor.fetchall()
        return {
            "schema_version": "1",
            "root_node_id": node_id,
            "direction": direction,
            "depth": depth,
            "truncated": truncated,
            "nodes": [
                {
                    "node_id": row[0],
                    "node_type": row[1],
                    "schema_version": row[2],
                    "properties": _safe_properties(row[3]),
                    "created_at": row[4].isoformat(),
                }
                for row in nodes
            ],
            "edges": [
                {
                    "edge_id": row[0],
                    "edge_type": row[1],
                    "source_node_id": row[2],
                    "target_node_id": row[3],
                    "properties": _safe_properties(row[4]),
                    "created_at": row[5].isoformat(),
                }
                for row in sorted(edges.values(), key=lambda item: (item[5], item[0]))
                if row[2] in seen and row[3] in seen
            ],
        }

    async def experiment_dossier(
        self,
        *,
        actor_key: str,
        experiment_id: str,
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT experiment_id, dataset_revision_id, specification_digest,
                       specification, status, progress_stage, cancellation,
                       result, error, created_at, updated_at
                FROM nexus_domain.experiments
                WHERE actor_key = %s AND experiment_id = %s
                """,
                (actor_key, experiment_id),
            )
            experiment = await cursor.fetchone()
            if experiment is None:
                return None
            cursor = await connection.execute(
                """
                SELECT dataset_revision_id, source_staging_revision_id,
                       manifest_hash, schema_version, status, limitations,
                       created_at, ready_at
                FROM nexus_domain.dataset_revisions
                WHERE actor_key = %s AND dataset_revision_id = %s
                """,
                (actor_key, experiment[1]),
            )
            dataset = await cursor.fetchone()
            cursor = await connection.execute(
                """
                SELECT relative_path, media_type, size_bytes, content_hash
                FROM nexus_domain.dataset_manifest_files
                WHERE actor_key = %s AND dataset_revision_id = %s
                ORDER BY relative_path
                LIMIT 1000
                """,
                (actor_key, experiment[1]),
            )
            dataset_files = await cursor.fetchall()
            cursor = await connection.execute(
                """
                SELECT attempt, started_at, finished_at, status, stage,
                       safe_error, runtime_versions
                FROM nexus_domain.job_attempts
                WHERE actor_key = %s AND experiment_id = %s
                ORDER BY attempt
                """,
                (actor_key, experiment_id),
            )
            attempts = await cursor.fetchall()
            cursor = await connection.execute(
                """
                SELECT manifest, (expires_at IS NOT NULL AND expires_at <= now()),
                       deleted_at
                FROM nexus_domain.artifacts
                WHERE actor_key = %s
                  AND manifest ->> 'producer_id' = %s
                ORDER BY created_at
                """,
                (actor_key, experiment_id),
            )
            artifacts = await cursor.fetchall()
            cursor = await connection.execute(
                """
                SELECT node_id, properties
                FROM nexus_domain.lineage_nodes
                WHERE actor_key = %s AND node_type = 'metric'
                  AND properties ->> 'experiment_id' = %s
                ORDER BY properties ->> 'metric_group', properties ->> 'name'
                """,
                (actor_key, experiment_id),
            )
            metrics = await cursor.fetchall()
            cursor = await connection.execute(
                """
                SELECT workflow_id, workflow_type, intent_digest, schema_version,
                       state, stage, normalized_input, output, error,
                       thread_id, run_id, created_at, updated_at
                FROM nexus_domain.workflows
                WHERE actor_key = %s AND output ->> 'experiment_id' = %s
                ORDER BY created_at
                LIMIT 1
                """,
                (actor_key, experiment_id),
            )
            workflow = await cursor.fetchone()
            workflow_transitions = []
            if workflow is not None:
                cursor = await connection.execute(
                    """
                    SELECT sequence, from_state, to_state, stage,
                           safe_metadata, occurred_at
                    FROM nexus_domain.workflow_transitions
                    WHERE actor_key = %s AND workflow_id = %s
                    ORDER BY sequence
                    """,
                    (actor_key, workflow[0]),
                )
                workflow_transitions = await cursor.fetchall()
            cursor = await connection.execute(
                """
                SELECT p.publication_id, p.publication_type, p.status,
                       p.target_reference, p.safe_error, p.evidence_references,
                       p.created_at, p.updated_at,
                       a.approval_request_id, a.status, a.human_summary,
                       a.requested_at, a.decided_at, a.execution_status,
                       a.executed_at, a.execution_reference, a.execution_error
                FROM nexus_domain.publication_effects p
                JOIN nexus_domain.approval_requests a
                  ON a.actor_key = p.actor_key
                 AND a.approval_request_id = p.approval_request_id
                WHERE p.actor_key = %s
                  AND p.evidence_references @> %s
                ORDER BY p.created_at, p.publication_id
                """,
                (actor_key, Jsonb([experiment_id])),
            )
            publication_rows = await cursor.fetchall()
            cursor = await connection.execute(
                """
                SELECT interpretation_report_id, schema_version, content_digest,
                       structured_content, evidence_references, mapping_origin,
                       thread_id, run_id, created_at
                FROM nexus_domain.interpretation_reports
                WHERE actor_key = %s AND experiment_id = %s
                ORDER BY created_at, interpretation_report_id
                """,
                (actor_key, experiment_id),
            )
            interpretation_rows = await cursor.fetchall()
            source_ids = sorted(
                {
                    str(reference)
                    for row in publication_rows
                    for reference in (row[5] or [])
                    if str(reference).startswith("src_v1_")
                }
                | {
                    str(reference)
                    for row in interpretation_rows
                    for reference in (row[4] or [])
                    if str(reference).startswith("src_v1_")
                }
            )
            sources = []
            if source_ids:
                cursor = await connection.execute(
                    """
                    SELECT source_record_id, normalized_locator, content_hash,
                           source_metadata, retrieval_status, retrieved_at
                    FROM nexus_domain.research_sources
                    WHERE actor_key = %s AND source_record_id = ANY(%s)
                    ORDER BY retrieved_at, source_record_id
                    """,
                    (actor_key, source_ids),
                )
                sources = await cursor.fetchall()
        limitations = dict(dataset[5] or {}) if dataset else {
            "production_eligibility": "blocked"
        }
        return {
            "schema_version": "1",
            "experiment": {
                "experiment_id": experiment[0],
                "dataset_revision_id": experiment[1],
                "specification_digest": experiment[2],
                "specification": experiment[3],
                "status": experiment[4],
                "stage": experiment[5],
                "cancellation": experiment[6],
                "error": experiment[8],
                "created_at": experiment[9].isoformat(),
                "updated_at": experiment[10].isoformat(),
            },
            "dataset": (
                {
                    "dataset_revision_id": dataset[0],
                    "source_staging_revision_id": dataset[1],
                    "manifest_hash": dataset[2],
                    "schema_version": dataset[3],
                    "status": dataset[4],
                    "limitations": limitations,
                    "created_at": dataset[6].isoformat(),
                    "ready_at": dataset[7].isoformat() if dataset[7] else None,
                    "files": [
                        {
                            "relative_path": row[0],
                            "media_type": row[1],
                            "size_bytes": row[2],
                            "content_hash": row[3],
                        }
                        for row in dataset_files
                    ],
                }
                if dataset
                else None
            ),
            "metrics": [_metric_evidence(row[0], row[1]) for row in metrics],
            "artifacts": [
                {
                    **_safe_properties(row[0]),
                    "storage_status": (
                        "deleted"
                        if row[2]
                        else "expired"
                        if row[1] is True
                        else row[0].get("storage_status", "ready")
                    ),
                }
                for row in artifacts
            ],
            "attempts": [
                {
                    "attempt": row[0],
                    "started_at": row[1].isoformat() if row[1] else None,
                    "finished_at": row[2].isoformat() if row[2] else None,
                    "status": row[3],
                    "stage": row[4],
                    "safe_error": row[5],
                    "runtime_versions": row[6],
                }
                for row in attempts
            ],
            "workflow": (
                {
                    "workflow_id": workflow[0],
                    "workflow_type": workflow[1],
                    "intent_digest": workflow[2],
                    "schema_version": workflow[3],
                    "state": workflow[4],
                    "stage": workflow[5],
                    "normalized_input": _safe_properties(workflow[6]),
                    "output": _safe_properties(workflow[7]),
                    "error": _safe_properties(workflow[8]),
                    "thread_id": workflow[9],
                    "run_id": workflow[10],
                    "created_at": workflow[11].isoformat(),
                    "updated_at": workflow[12].isoformat(),
                    "transitions": [
                        {
                            "sequence": row[0],
                            "from_state": row[1],
                            "to_state": row[2],
                            "stage": row[3],
                            "metadata": _safe_properties(row[4]),
                            "occurred_at": row[5].isoformat(),
                        }
                        for row in workflow_transitions
                    ],
                }
                if workflow is not None
                else None
            ),
            "publications": [
                {
                    "publication_id": row[0],
                    "publication_type": row[1],
                    "status": row[2],
                    "target_reference": _safe_properties(row[3]),
                    "safe_error": _safe_properties(row[4]),
                    "evidence_references": row[5],
                    "created_at": row[6].isoformat(),
                    "updated_at": row[7].isoformat(),
                    "approval": {
                        "approval_request_id": row[8],
                        "status": row[9],
                        "human_summary": _safe_properties(row[10]),
                        "requested_at": row[11].isoformat(),
                        "decided_at": row[12].isoformat() if row[12] else None,
                        "execution_status": row[13],
                        "executed_at": row[14].isoformat() if row[14] else None,
                        "execution_reference": _safe_properties(row[15]),
                        "execution_error": _safe_properties(row[16]),
                    },
                }
                for row in publication_rows
            ],
            "interpretations": [
                {
                    "interpretation_report_id": row[0],
                    "schema_version": row[1],
                    "content_digest": row[2],
                    "content": _safe_properties(row[3]),
                    "evidence_references": row[4],
                    "mapping_origin": row[5],
                    "thread_id": row[6],
                    "run_id": row[7],
                    "created_at": row[8].isoformat(),
                }
                for row in interpretation_rows
            ],
            "sources": [
                {
                    "source_record_id": row[0],
                    "locator": row[1],
                    "content_hash": row[2],
                    "metadata": _safe_properties(row[3]),
                    "retrieval_status": row[4],
                    "retrieved_at": row[5].isoformat(),
                }
                for row in sources
            ],
            "limitations": limitations,
            "production_eligibility": limitations.get(
                "production_eligibility", "blocked"
            ),
            "evidence_gaps": _dossier_evidence_gaps(
                dataset=dataset,
                metrics=metrics,
                workflow=workflow,
                interpretations=interpretation_rows,
            ),
            "lineage": {"root_node_id": experiment_id},
        }

    async def compare_experiments(
        self,
        *,
        actor_key: str,
        experiment_ids: list[str],
    ) -> dict[str, Any] | None:
        dossiers: list[dict[str, Any]] = []
        for experiment_id in experiment_ids:
            dossier = await self.experiment_dossier(
                actor_key=actor_key,
                experiment_id=experiment_id,
            )
            if dossier is None:
                return None
            dossiers.append(dossier)
        reasons = _comparison_incompatibilities(dossiers)
        configurations = [
            {
                "experiment_id": item["experiment"]["experiment_id"],
                "dataset_revision_id": item["experiment"]["dataset_revision_id"],
                "manifest_hash": (item.get("dataset") or {}).get("manifest_hash"),
                "universe": item["experiment"]["specification"].get("universe"),
                "feature_set": item["experiment"]["specification"].get("feature_set"),
                "model": item["experiment"]["specification"].get("model"),
                "segments": item["experiment"]["specification"].get("train"),
                "valid_segment": item["experiment"]["specification"].get("valid"),
                "test_segment": item["experiment"]["specification"].get("test"),
                "strategy": item["experiment"]["specification"].get("strategy"),
                "limitations": item.get("limitations"),
            }
            for item in dossiers
        ]
        metric_names = sorted(
            {
                (metric.get("metric_group"), metric.get("name"))
                for item in dossiers
                for metric in item.get("metrics", [])
            }
        )
        metrics = []
        if not reasons:
            for group, name in metric_names:
                by_experiment = {}
                definition = None
                calculation_version = None
                for item in dossiers:
                    match = next(
                        (
                            metric
                            for metric in item.get("metrics", [])
                            if metric.get("metric_group") == group
                            and metric.get("name") == name
                        ),
                        None,
                    )
                    by_experiment[item["experiment"]["experiment_id"]] = (
                        match.get("value") if match else None
                    )
                    if match:
                        definition = definition or match.get("meaning")
                        calculation_version = calculation_version or match.get(
                            "calculation_version"
                        )
                metrics.append(
                    {
                        "metric_group": group,
                        "name": name,
                        "meaning": definition,
                        "calculation_version": calculation_version,
                        "values": by_experiment,
                    }
                )
        return {
            "schema_version": "1",
            "compatible": not reasons,
            "incompatibilities": reasons,
            "configurations": configurations,
            "metrics": metrics,
        }

    async def export_dossier(
        self,
        *,
        actor_key: str,
        experiment_id: str,
    ) -> dict[str, Any] | None:
        dossier = await self.experiment_dossier(
            actor_key=actor_key,
            experiment_id=experiment_id,
        )
        if dossier is None:
            return None
        evidence_ids = _dossier_evidence_ids(dossier)
        payload = {
            "schema_version": "1",
            "experiment_id": experiment_id,
            "dossier": dossier,
            "included_evidence_ids": evidence_ids,
            "exclusions": [
                "artifact_bodies",
                "credentials",
                "private_memory",
                "raw_checkpoints",
                "runtime_prompts",
            ],
        }
        digest = content_hash("dossier_export", payload)
        export_id = new_runtime_id("dossier_export")
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO nexus_domain.dossier_exports
                    (actor_key, export_id, experiment_id,
                     dossier_schema_version, included_evidence_ids, content_hash)
                VALUES (%s, %s, %s, '1', %s, %s)
                RETURNING created_at
                """,
                (
                    actor_key,
                    export_id,
                    experiment_id,
                    Jsonb(evidence_ids),
                    digest,
                ),
            )
            created_at = (await cursor.fetchone())[0]
        return {
            **payload,
            "export_id": export_id,
            "content_hash": digest,
            "created_at": created_at.isoformat(),
        }


def _safe_properties(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_properties(item)
            for key, item in value.items()
            if str(key).lower()
            not in {
                "storage_key",
                "provider_uri",
                "authorization",
                "credential",
                "password",
                "secret",
                "access_token",
                "refresh_token",
            }
        }
    if isinstance(value, list):
        return [_safe_properties(item) for item in value]
    return value


def _metric_evidence(metric_id: str, properties: Any) -> dict[str, Any]:
    safe = _safe_properties(properties or {})
    missing = [
        field
        for field in (
            "dataset_revision_id",
            "specification_digest",
            "attempt_id",
            "calculation_version",
            "meaning",
        )
        if not safe.get(field)
    ]
    if not safe.get("artifact_ids"):
        missing.append("artifact_ids")
    return {
        "metric_id": metric_id,
        **safe,
        "evidence_status": "complete" if not missing else "incomplete",
        "missing_evidence": missing,
    }


def _dossier_evidence_gaps(
    *,
    dataset: Any,
    metrics: list[Any],
    workflow: Any,
    interpretations: list[Any],
) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    if dataset is None:
        gaps.append(
            {"code": "dataset_missing", "message": "Dataset evidence is unavailable."}
        )
    if workflow is None:
        gaps.append(
            {
                "code": "workflow_request_unlinked",
                "message": "The originating normalized workflow request is unavailable.",
            }
        )
    incomplete_metrics = [
        row[0]
        for row in metrics
        if _metric_evidence(row[0], row[1])["evidence_status"] != "complete"
    ]
    if incomplete_metrics:
        gaps.append(
            {
                "code": "metric_evidence_incomplete",
                "message": f"{len(incomplete_metrics)} metric(s) have incomplete evidence.",
            }
        )
    if not interpretations:
        gaps.append(
            {
                "code": "interpretation_missing",
                "message": "No persisted model-assisted interpretation is linked.",
            }
        )
    return gaps


def _comparison_incompatibilities(
    dossiers: list[dict[str, Any]],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []

    def values(path):
        return {path(item) for item in dossiers}

    if len(values(lambda item: item["experiment"]["dataset_revision_id"])) != 1:
        reasons.append(
            {
                "code": "dataset_revision_mismatch",
                "message": "Experiments use different dataset revisions.",
            }
        )
    specifications = [item["experiment"]["specification"] for item in dossiers]
    checks = (
        ("universe", "universe_mismatch"),
        ("feature_set", "feature_set_mismatch"),
        ("train", "train_segment_mismatch"),
        ("valid", "valid_segment_mismatch"),
        ("test", "test_segment_mismatch"),
    )
    for field, code in checks:
        serialized = {
            repr(_safe_properties(specification.get(field)))
            for specification in specifications
        }
        if len(serialized) != 1:
            reasons.append(
                {
                    "code": code,
                    "message": f"Experiment {field} configurations are incompatible.",
                }
            )
    label_horizons = {
        ((item.get("limitations") or {}).get("lookahead") or {}).get(
            "label_lookahead_trading_days"
        )
        for item in dossiers
    }
    if len(label_horizons) != 1:
        reasons.append(
            {
                "code": "label_horizon_mismatch",
                "message": "Label look-ahead horizons are incompatible.",
            }
        )
    versions: dict[tuple[Any, Any], set[Any]] = {}
    for item in dossiers:
        for metric in item.get("metrics", []):
            key = (metric.get("metric_group"), metric.get("name"))
            versions.setdefault(key, set()).add(metric.get("calculation_version"))
    if any(len(items) > 1 for items in versions.values()):
        reasons.append(
            {
                "code": "metric_calculation_version_mismatch",
                "message": "Metric calculation versions are incompatible.",
            }
        )
    return reasons


def _dossier_evidence_ids(dossier: dict[str, Any]) -> list[str]:
    identifiers: set[str] = {
        dossier["experiment"]["experiment_id"],
        dossier["experiment"]["dataset_revision_id"],
    }
    if workflow := dossier.get("workflow"):
        identifiers.add(workflow["workflow_id"])
    identifiers.update(
        metric["metric_id"] for metric in dossier.get("metrics", [])
    )
    identifiers.update(
        artifact["artifact_id"]
        for artifact in dossier.get("artifacts", [])
        if artifact.get("artifact_id")
    )
    identifiers.update(
        source["source_record_id"] for source in dossier.get("sources", [])
    )
    identifiers.update(
        report["interpretation_report_id"]
        for report in dossier.get("interpretations", [])
    )
    identifiers.update(
        publication["publication_id"]
        for publication in dossier.get("publications", [])
    )
    return sorted(identifiers)
