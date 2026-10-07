"""Actor-scoped experiment catalog reads with stable keyset pagination."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from psycopg import sql


EXPERIMENT_STATUSES = frozenset(
    {
        "queued",
        "leased",
        "running",
        "cancelling",
        "cancelled",
        "completed",
        "failed",
        "expired",
    }
)
_EXPERIMENT_ID = re.compile(r"^exp_v1_[0-9a-f]{32}$")
_MAX_CURSOR_LENGTH = 512


def encode_experiment_cursor(created_at: datetime, experiment_id: str) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "created_at": created_at.isoformat(),
            "experiment_id": experiment_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_experiment_cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    if not value or len(value) > _MAX_CURSOR_LENGTH:
        raise ValueError("Invalid experiment cursor.")
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(
            base64.b64decode(value + padding, altchars=b"-_", validate=True)
        )
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError
        created_at = datetime.fromisoformat(str(payload.get("created_at", "")))
        experiment_id = str(payload.get("experiment_id", ""))
        if created_at.tzinfo is None or not _EXPERIMENT_ID.fullmatch(experiment_id):
            raise ValueError
        return created_at, experiment_id
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid experiment cursor.") from exc


class PostgresExperimentCatalogRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def list_experiments(
        self,
        *,
        actor_key: str,
        limit: int = 20,
        statuses: Iterable[str] = (),
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("Experiment catalog limit must be between 1 and 100.")
        normalized_statuses = tuple(dict.fromkeys(str(item) for item in statuses))
        if any(status not in EXPERIMENT_STATUSES for status in normalized_statuses):
            raise ValueError("Invalid experiment status filter.")
        cursor_value = decode_experiment_cursor(cursor)

        conditions = [sql.SQL("actor_key = %s")]
        parameters: list[Any] = [actor_key]
        if normalized_statuses:
            conditions.append(sql.SQL("status = ANY(%s)"))
            parameters.append(list(normalized_statuses))
        if cursor_value is not None:
            conditions.append(sql.SQL("(created_at, experiment_id) < (%s, %s)"))
            parameters.extend(cursor_value)
        parameters.append(limit + 1)

        async with self.pool.connection() as connection:
            result = await connection.execute(
                sql.SQL("""
                SELECT experiment_id, dataset_revision_id, status, progress_stage,
                       specification, created_at, updated_at
                FROM nexus_domain.experiments
                WHERE {}
                ORDER BY created_at DESC, experiment_id DESC
                LIMIT %s
                """).format(sql.SQL(" AND ").join(conditions)),
                tuple(parameters),
            )
            rows = await result.fetchall()

        page_rows = rows[:limit]
        next_cursor = (
            encode_experiment_cursor(page_rows[-1][5], str(page_rows[-1][0]))
            if len(rows) > limit and page_rows
            else None
        )
        return {
            "schema_version": "1",
            "items": [_catalog_item(row) for row in page_rows],
            "next_cursor": next_cursor,
        }


def _catalog_item(row: tuple[Any, ...]) -> dict[str, Any]:
    specification = row[4] if isinstance(row[4], Mapping) else {}
    strategy = specification.get("strategy")
    strategy_values = strategy if isinstance(strategy, Mapping) else {}
    test = specification.get("test")
    test_values = test if isinstance(test, Mapping) else {}
    return {
        "experiment_id": str(row[0]),
        "dataset_revision_id": str(row[1]),
        "status": str(row[2]),
        "progress_stage": str(row[3]) if row[3] is not None else None,
        "created_at": row[5].isoformat(),
        "updated_at": row[6].isoformat(),
        "specification_summary": {
            "universe": _optional_string(specification.get("universe")),
            "model": _optional_string(specification.get("model")),
            "feature_set": _optional_string(specification.get("feature_set")),
            "strategy_type": _optional_string(strategy_values.get("type")),
            "test_start": _optional_string(test_values.get("start")),
            "test_end": _optional_string(test_values.get("end")),
        },
    }


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
