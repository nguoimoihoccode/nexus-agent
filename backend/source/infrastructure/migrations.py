"""Explicit PostgreSQL domain migration and compatibility commands."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg import sql

DOMAIN_SCHEMA_VERSION = 14
_MIGRATION_LOCK = 721_994_031
_MIGRATION_ROOT = Path(__file__).resolve().parents[2] / "migrations" / "domain"
_ROLE_SQL = Path(__file__).resolve().parents[2] / "migrations" / "admin" / "roles.sql"
_ACCEPTED_LEGACY_CHECKSUMS = {
    # Migration 0008 was briefly published with the future 0011 table appended.
    # Accept only that exact accidental checksum so affected beta databases can
    # advance to the canonical forward migration without weakening validation.
    (8, "user_memory"): frozenset(
        {"45e927115e03110205745f1f0736e3471693f886ee58c81a4939c4298b334e63"}  # pragma: allowlist secret
    ),
}


class MigrationError(RuntimeError):
    """A safe migration or compatibility failure."""


def _row_value(row: Any, key: str, index: int) -> Any:
    """Read both psycopg tuple rows and the shared pool's dict rows."""
    if isinstance(row, Mapping):
        return row.get(key)
    return row[index]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up_path: Path
    down_path: Path
    checksum: str

    @property
    def up_sql(self) -> str:
        return self.up_path.read_text(encoding="utf-8")

    @property
    def down_sql(self) -> str:
        return self.down_path.read_text(encoding="utf-8")


def discover_migrations(root: Path = _MIGRATION_ROOT) -> tuple[Migration, ...]:
    migrations: list[Migration] = []
    for up_path in sorted(root.glob("*_*.up.sql")):
        version_text, _, remainder = up_path.name.partition("_")
        name = remainder.removesuffix(".up.sql")
        down_path = up_path.with_name(f"{version_text}_{name}.down.sql")
        if not down_path.is_file():
            raise MigrationError(f"Missing rollback file for migration {up_path.name}.")
        raw = up_path.read_bytes()
        migrations.append(
            Migration(
                version=int(version_text),
                name=name,
                up_path=up_path,
                down_path=down_path,
                checksum=hashlib.sha256(raw).hexdigest(),
            )
        )
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise MigrationError("Domain migration versions must be contiguous from 1.")
    return tuple(migrations)


def _ensure_migration_table(connection: Any) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS nexus_domain")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS nexus_domain.schema_migrations (
            version integer PRIMARY KEY,
            name text NOT NULL,
            checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def _applied(connection: Any) -> dict[int, tuple[str, str]]:
    rows = connection.execute(
        "SELECT version, name, checksum FROM nexus_domain.schema_migrations "
        "ORDER BY version"
    ).fetchall()
    return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}


def _matches_applied_migration(
    migration: Migration,
    existing: tuple[str, str],
) -> bool:
    name, checksum = existing
    if name != migration.name:
        return False
    return checksum == migration.checksum or checksum in _ACCEPTED_LEGACY_CHECKSUMS.get(
        (migration.version, migration.name), frozenset()
    )


def upgrade(connection: Any, migrations: Iterable[Migration] | None = None) -> list[int]:
    """Apply pending expand migrations under one PostgreSQL advisory lock."""
    available = tuple(migrations or discover_migrations())
    applied_now: list[int] = []
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        _ensure_migration_table(connection)
        applied = _applied(connection)
        for migration in available:
            existing = applied.get(migration.version)
            if existing:
                if not _matches_applied_migration(migration, existing):
                    raise MigrationError(
                        f"Migration {migration.version} checksum or name changed."
                    )
                continue
            connection.execute(migration.up_sql)
            connection.execute(
                "INSERT INTO nexus_domain.schema_migrations "
                "(version, name, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
            applied_now.append(migration.version)
    return applied_now


def rollback_latest(
    connection: Any,
    migrations: Iterable[Migration] | None = None,
) -> int:
    """Rollback one migration; callers must opt in at the CLI boundary."""
    available = {item.version: item for item in (migrations or discover_migrations())}
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        _ensure_migration_table(connection)
        applied = _applied(connection)
        if not applied:
            raise MigrationError("No domain migration is applied.")
        version = max(applied)
        migration = available.get(version)
        if migration is None:
            raise MigrationError(f"Rollback SQL for migration {version} is unavailable.")
        if applied[version] != (migration.name, migration.checksum):
            raise MigrationError(f"Migration {version} checksum or name changed.")
        connection.execute(migration.down_sql)
        if version > 1:
            connection.execute(
                "DELETE FROM nexus_domain.schema_migrations WHERE version = %s",
                (version,),
            )
    return version


def status(connection: Any) -> dict[str, Any]:
    _ensure_migration_table(connection)
    available = discover_migrations()
    applied = _applied(connection)
    return {
        "required_version": DOMAIN_SCHEMA_VERSION,
        "latest_available": available[-1].version if available else 0,
        "applied": [
            {"version": version, "name": name, "checksum": checksum}
            for version, (name, checksum) in applied.items()
        ],
    }


def provision_roles(
    connection: Any,
    login_roles: Iterable[tuple[str, str, str]] = (),
) -> list[str]:
    """Apply group grants and create/rotate deployment-owned least-privilege logins."""
    allowed_groups = {
        "nexus_backend_role",
        "nexus_quant_data_role",
        "nexus_quant_worker_role",
    }
    requested_roles = tuple(login_roles)
    usernames: set[str] = set()
    for username, password, group_role in requested_roles:
        if not username or not password:
            raise MigrationError("Database login usernames and passwords are required.")
        if group_role not in allowed_groups:
            raise MigrationError("Unknown database group role.")
        if username in allowed_groups:
            raise MigrationError("Database login cannot reuse a Nexus group role name.")
        if username in usernames:
            raise MigrationError("Database login usernames must be distinct.")
        usernames.add(username)

    provisioned: list[str] = []
    with connection.transaction():
        connection.execute(_ROLE_SQL.read_text(encoding="utf-8"))
        current_user = str(connection.execute("SELECT current_user").fetchone()[0])
        for username, password, group_role in requested_roles:
            if username == current_user:
                raise MigrationError(
                    "Migration administrator cannot be reused as an application login."
                )
            existing = connection.execute(
                """
                SELECT oid, rolsuper, rolcreatedb, rolcreaterole,
                       rolreplication, rolbypassrls
                FROM pg_roles WHERE rolname = %s
                """,
                (username,),
            ).fetchone()
            if existing and any(bool(value) for value in existing[1:]):
                raise MigrationError(
                    "Privileged database role cannot be reused as an application login."
                )
            if existing:
                memberships = {
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT parent.rolname
                        FROM pg_auth_members membership
                        JOIN pg_roles parent ON parent.oid = membership.roleid
                        WHERE membership.member = %s
                        """,
                        (existing[0],),
                    ).fetchall()
                }
                if memberships - allowed_groups:
                    raise MigrationError(
                        "Application login has an unmanaged database role membership."
                    )
                connection.execute(
                    sql.SQL(
                        "ALTER ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                    ).format(
                        sql.Identifier(username),
                        sql.Literal(password),
                    )
                )
            else:
                connection.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                    ).format(
                        sql.Identifier(username),
                        sql.Literal(password),
                    )
                )
            connection.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.SQL(", ").join(
                        sql.Identifier(role) for role in sorted(allowed_groups)
                    ),
                    sql.Identifier(username),
                )
            )
            connection.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(group_role),
                    sql.Identifier(username),
                )
            )
            provisioned.append(username)
    return provisioned


def _login_roles_from_environment() -> list[tuple[str, str, str]]:
    definitions = (
        ("NEXUS_BACKEND_DB", "nexus_backend_role"),
        ("NEXUS_QUANT_DATA_DB", "nexus_quant_data_role"),
        ("NEXUS_QUANT_WORKER_DB", "nexus_quant_worker_role"),
    )
    roles: list[tuple[str, str, str]] = []
    for prefix, group in definitions:
        username = os.environ.get(f"{prefix}_USER", "").strip()
        password = os.environ.get(f"{prefix}_PASSWORD", "")
        if username or password:
            roles.append((username, password, group))
    return roles


async def verify_domain_schema(pool: Any) -> None:
    """Fail startup when the domain schema is absent, old, or unexpectedly new."""
    migrations = await asyncio.to_thread(discover_migrations)
    expected = migrations[-1] if migrations else None
    async with pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT to_regclass('nexus_domain.schema_migrations')"
        )
        row = await cursor.fetchone()
        if not row or not _row_value(row, "to_regclass", 0):
            raise MigrationError(
                "Domain schema is not initialized; run the explicit migration command."
            )
        cursor = await connection.execute(
            "SELECT version, name, checksum FROM nexus_domain.schema_migrations "
            "ORDER BY version DESC LIMIT 1"
        )
        latest = await cursor.fetchone()
    if expected is None or latest is None:
        raise MigrationError("No compatible domain schema migration is installed.")
    actual = (
        int(_row_value(latest, "version", 0)),
        str(_row_value(latest, "name", 1)),
        str(_row_value(latest, "checksum", 2)),
    )
    required = (expected.version, expected.name, expected.checksum)
    if actual != required or actual[0] != DOMAIN_SCHEMA_VERSION:
        raise MigrationError(
            f"Domain schema version {actual[0]} is incompatible; "
            f"required version is {DOMAIN_SCHEMA_VERSION}."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("status", "upgrade", "provision-roles", "bootstrap", "rollback"),
    )
    parser.add_argument(
        "--allow-destructive",
        action="store_true",
        help="Required for an explicit one-step rollback.",
    )
    args = parser.parse_args()
    if args.command == "rollback" and not args.allow_destructive:
        parser.error("rollback requires --allow-destructive")
    postgres_url = os.environ.get("POSTGRES_URI", "").strip()
    if not postgres_url:
        from source.core.config import settings

        postgres_url = settings.postgres_url
    with psycopg.connect(postgres_url) as connection:
        if args.command == "upgrade":
            result: Any = {"applied": upgrade(connection)}
        elif args.command == "provision-roles":
            result = {"provisioned": provision_roles(connection, _login_roles_from_environment())}
        elif args.command == "bootstrap":
            result = {
                "applied": upgrade(connection),
                "provisioned": provision_roles(
                    connection,
                    _login_roles_from_environment(),
                ),
            }
        elif args.command == "rollback":
            result = {"rolled_back": rollback_latest(connection)}
        else:
            result = status(connection)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
