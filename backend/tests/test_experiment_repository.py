from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from source.infrastructure.experiment_repository import (
    PostgresExperimentCatalogRepository,
    decode_experiment_cursor,
    encode_experiment_cursor,
)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""
        self.parameters = ()

    async def execute(self, query, parameters):
        self.query = query.as_string() if hasattr(query, "as_string") else query
        self.parameters = parameters
        return _Cursor(self.rows)


class _ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, rows):
        self.connection_value = _Connection(rows)

    def connection(self):
        return _ConnectionContext(self.connection_value)


class ExperimentCatalogRepositoryTests(unittest.TestCase):
    def test_cursor_round_trip_and_malformed_values_fail_closed(self) -> None:
        created_at = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        experiment_id = "exp_v1_" + "a" * 32

        encoded = encode_experiment_cursor(created_at, experiment_id)

        self.assertEqual(
            decode_experiment_cursor(encoded),
            (created_at, experiment_id),
        )
        for malformed in ("", "not-base64!", "e30", "a" * 513):
            with self.assertRaisesRegex(ValueError, "Invalid experiment cursor"):
                decode_experiment_cursor(malformed)

    def test_catalog_is_filtered_ordered_and_returns_a_next_cursor(self) -> None:
        first_at = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        second_at = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        rows = [
            (
                "exp_v1_" + "a" * 32,
                "ds-first",
                "completed",
                "finished",
                {
                    "universe": "csi300",
                    "model": "linear",
                    "feature_set": "Alpha158",
                    "strategy": {"type": "topk_dropout"},
                    "test": {"start": "2025-01-01", "end": "2025-03-31"},
                },
                first_at,
                first_at,
            ),
            (
                "exp_v1_" + "b" * 32,
                "ds-second",
                "completed",
                None,
                {},
                second_at,
                second_at,
            ),
        ]
        pool = _Pool(rows)
        repository = PostgresExperimentCatalogRepository(pool)

        page = asyncio.run(
            repository.list_experiments(
                actor_key="v1-" + "c" * 64,
                limit=1,
                statuses=("completed",),
            )
        )

        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["specification_summary"]["model"], "linear")
        self.assertEqual(
            decode_experiment_cursor(page["next_cursor"]),
            (first_at, "exp_v1_" + "a" * 32),
        )
        self.assertIn("status = ANY(%s)", pool.connection_value.query)
        self.assertIn(
            "ORDER BY created_at DESC, experiment_id DESC",
            pool.connection_value.query,
        )
        self.assertEqual(pool.connection_value.parameters[-2:], (["completed"], 2))

    def test_catalog_rejects_unknown_status(self) -> None:
        repository = PostgresExperimentCatalogRepository(_Pool([]))

        with self.assertRaisesRegex(ValueError, "Invalid experiment status"):
            asyncio.run(
                repository.list_experiments(
                    actor_key="v1-" + "d" * 64,
                    statuses=("unknown",),
                )
            )


if __name__ == "__main__":
    unittest.main()
