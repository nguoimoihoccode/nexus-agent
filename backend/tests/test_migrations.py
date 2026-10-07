"""Focused tests for domain schema compatibility checks."""

import asyncio
import unittest

from source.infrastructure.migrations import (
    _matches_applied_migration,
    discover_migrations,
    verify_domain_schema,
)


class _Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, schema_row, latest_row):
        self.rows = iter((schema_row, latest_row))

    async def execute(self, _query):
        return _Cursor(next(self.rows))


class _ConnectionContext:
    def __init__(self, connection):
        self.connection_value = connection

    async def __aenter__(self):
        return self.connection_value

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, schema_row, latest_row):
        self.connection_value = _Connection(schema_row, latest_row)

    def connection(self):
        return _ConnectionContext(self.connection_value)


class MigrationCompatibilityTests(unittest.TestCase):
    def test_only_known_accidental_user_memory_checksum_is_accepted(self):
        migration = discover_migrations()[7]

        self.assertTrue(
            _matches_applied_migration(
                migration,
                (
                    "user_memory",
                    "45e927115e03110205745f1f0736e3471693f886ee58c81a4939c4298b334e63",  # pragma: allowlist secret
                ),
            )
        )
        self.assertFalse(
            _matches_applied_migration(migration, ("user_memory", "0" * 64))
        )
        self.assertFalse(
            _matches_applied_migration(migration, ("renamed_memory", migration.checksum))
        )

    def test_verify_domain_schema_accepts_shared_dict_rows(self):
        expected = discover_migrations()[-1]
        pool = _Pool(
            {"to_regclass": "nexus_domain.schema_migrations"},
            {
                "version": expected.version,
                "name": expected.name,
                "checksum": expected.checksum,
            },
        )

        asyncio.run(verify_domain_schema(pool))


if __name__ == "__main__":
    unittest.main()
