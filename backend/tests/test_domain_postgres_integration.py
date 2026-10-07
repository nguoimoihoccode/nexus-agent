"""PostgreSQL integration checks; enabled with NEXUS_TEST_POSTGRES_URI."""

from __future__ import annotations

import asyncio
import os
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from source.domain import (
    ApprovalConflict,
    IdempotencyConflict,
    PublicationConflict,
    WorkflowConflict,
    approval_action_identity,
    normalize_governed_arguments,
)
from source.infrastructure.approval_repository import PostgresApprovalRepository
from source.infrastructure.authorization_repository import (
    PostgresAuthorizationLeaseRepository,
)
from source.infrastructure.domain_repository import PostgresDomainControlRepository
from source.infrastructure.event_repository import PostgresProductEventRepository
from source.infrastructure.experiment_repository import (
    PostgresExperimentCatalogRepository,
)
from source.infrastructure.lineage_repository import PostgresLineageRepository
from source.infrastructure.memory_repository import PostgresUserMemoryRepository
from source.infrastructure.interpretation_repository import (
    InterpretationConflict,
    PostgresInterpretationRepository,
)
from source.infrastructure.migrations import (
    MigrationError,
    discover_migrations,
    provision_roles,
    rollback_latest,
    upgrade,
)
from source.infrastructure.publication_repository import PostgresPublicationRepository
from source.infrastructure.source_repository import PostgresResearchSourceRepository
from source.infrastructure.workflow_repository import PostgresWorkflowRepository

POSTGRES_URI = os.environ.get("NEXUS_TEST_POSTGRES_URI", "").strip()


@unittest.skipUnless(POSTGRES_URI, "NEXUS_TEST_POSTGRES_URI is not configured")
class DomainMigrationIntegrationTests(unittest.TestCase):
    def test_fresh_upgrade_rollback_and_forward_fix(self) -> None:
        database_name = f"nexus_migration_test_{uuid4().hex}"
        parts = conninfo_to_dict(POSTGRES_URI)
        maintenance = make_conninfo(**{**parts, "dbname": "postgres"})
        test_uri = make_conninfo(**{**parts, "dbname": database_name})
        with psycopg.connect(maintenance, autocommit=True) as connection:
            connection.execute(f'CREATE DATABASE "{database_name}"')
        try:
            with psycopg.connect(test_uri) as connection:
                self.assertEqual(
                    upgrade(connection),
                    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
                )
                memory_tables = connection.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'nexus_domain'
                      AND table_name IN ('user_memories', 'user_memory_items')
                    ORDER BY table_name
                    """
                ).fetchall()
                self.assertEqual(
                    memory_tables,
                    [("user_memories",), ("user_memory_items",)],
                )
                self.assertEqual(upgrade(connection), [])
                self.assertEqual(rollback_latest(connection), 14)
                missing_browser_sessions = connection.execute(
                    "SELECT to_regclass('nexus_domain.browser_sessions')"
                ).fetchone()[0]
                self.assertIsNone(missing_browser_sessions)
                self.assertEqual(upgrade(connection), [14])
                self.assertEqual(rollback_latest(connection), 14)
                self.assertEqual(rollback_latest(connection), 13)
                missing_catalog_index = connection.execute(
                    "SELECT to_regclass('nexus_domain.experiments_actor_created_idx')"
                ).fetchone()[0]
                self.assertIsNone(missing_catalog_index)
                self.assertEqual(upgrade(connection), [13, 14])
                self.assertEqual(rollback_latest(connection), 14)
                self.assertEqual(rollback_latest(connection), 13)
                self.assertEqual(rollback_latest(connection), 12)
                missing_leases = connection.execute(
                    "SELECT to_regclass('nexus_domain.authorization_leases')"
                ).fetchone()[0]
                self.assertIsNone(missing_leases)
                self.assertEqual(upgrade(connection), [12, 13, 14])
                self.assertEqual(rollback_latest(connection), 14)
                self.assertEqual(rollback_latest(connection), 13)
                self.assertEqual(rollback_latest(connection), 12)
                self.assertEqual(rollback_latest(connection), 11)
                missing_items = connection.execute(
                    "SELECT to_regclass('nexus_domain.user_memory_items')"
                ).fetchone()[0]
                self.assertIsNone(missing_items)
                self.assertEqual(upgrade(connection), [11, 12, 13, 14])
                self.assertEqual(rollback_latest(connection), 14)
                self.assertEqual(rollback_latest(connection), 13)
                self.assertEqual(rollback_latest(connection), 12)
                self.assertEqual(rollback_latest(connection), 11)
                self.assertEqual(rollback_latest(connection), 10)
                rls_enabled = connection.execute(
                    """
                    SELECT relrowsecurity
                    FROM pg_class
                    WHERE oid = 'nexus_domain.user_memories'::regclass
                    """
                ).fetchone()[0]
                self.assertFalse(rls_enabled)
                self.assertEqual(upgrade(connection), [10, 11, 12, 13, 14])
                tables = connection.execute(
                    """
                    SELECT count(*) FROM information_schema.tables
                    WHERE table_schema = 'nexus_domain'
                    """
                ).fetchone()[0]
                self.assertGreaterEqual(tables, 22)
        finally:
            with psycopg.connect(maintenance, autocommit=True) as connection:
                connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s",
                    (database_name,),
                )
                connection.execute(f'DROP DATABASE "{database_name}"')

    def test_accidental_migration_8_database_advances_through_forward_fix(self) -> None:
        database_name = f"nexus_migration_legacy_test_{uuid4().hex}"
        parts = conninfo_to_dict(POSTGRES_URI)
        maintenance = make_conninfo(**{**parts, "dbname": "postgres"})
        test_uri = make_conninfo(**{**parts, "dbname": database_name})
        with psycopg.connect(maintenance, autocommit=True) as connection:
            connection.execute(f'CREATE DATABASE "{database_name}"')
        try:
            migrations = discover_migrations()
            with psycopg.connect(test_uri) as connection:
                self.assertEqual(upgrade(connection, migrations[:8]), list(range(1, 9)))
                table_only_sql = migrations[10].up_sql.split("\n\nDO $$", 1)[0]
                connection.execute(table_only_sql)
                connection.execute(
                    """
                    UPDATE nexus_domain.schema_migrations
                    SET checksum = %s
                    WHERE version = 8
                    """,
                    (
                        "45e927115e03110205745f1f0736e3471693f886ee58c81a4939c4298b334e63",  # pragma: allowlist secret
                    ),
                )
                connection.commit()

                self.assertEqual(upgrade(connection), [9, 10, 11, 12, 13, 14])
                policy = connection.execute(
                    """
                    SELECT count(*) FROM pg_policies
                    WHERE schemaname = 'nexus_domain'
                      AND tablename = 'user_memory_items'
                      AND policyname = 'nexus_actor_isolation'
                    """
                ).fetchone()[0]
                rls_enabled = connection.execute(
                    """
                    SELECT relrowsecurity
                    FROM pg_class
                    WHERE oid = 'nexus_domain.user_memory_items'::regclass
                    """
                ).fetchone()[0]
                self.assertEqual(policy, 1)
                self.assertTrue(rls_enabled)
        finally:
            with psycopg.connect(maintenance, autocommit=True) as connection:
                connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s",
                    (database_name,),
                )
                connection.execute(f'DROP DATABASE "{database_name}"')

    def test_login_provisioning_is_distinct_and_least_privilege(self) -> None:
        suffix = uuid4().hex
        logins = [
            (f"nexus_backend_{suffix}", "backend-test-password", "nexus_backend_role"),
            (
                f"nexus_quant_data_{suffix}",
                "quant-data-test-password",
                "nexus_quant_data_role",
            ),
            (
                f"nexus_quant_worker_{suffix}",
                "quant-worker-test-password",
                "nexus_quant_worker_role",
            ),
        ]
        with psycopg.connect(POSTGRES_URI) as connection:
            upgrade(connection)
            try:
                self.assertEqual(
                    provision_roles(connection, logins),
                    [item[0] for item in logins],
                )
                for username, _password, expected_group in logins:
                    role = connection.execute(
                        """
                        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                               rolreplication, rolbypassrls
                        FROM pg_roles WHERE rolname = %s
                        """,
                        (username,),
                    ).fetchone()
                    memberships = {
                        str(row[0])
                        for row in connection.execute(
                            """
                            SELECT parent.rolname
                            FROM pg_auth_members membership
                            JOIN pg_roles member ON member.oid = membership.member
                            JOIN pg_roles parent ON parent.oid = membership.roleid
                            WHERE member.rolname = %s
                            """,
                            (username,),
                        ).fetchall()
                    }
                    self.assertEqual(role, (True, False, False, False, False, False))
                    self.assertEqual(memberships, {expected_group})

                with self.assertRaisesRegex(MigrationError, "distinct"):
                    provision_roles(
                        connection,
                        [
                            (logins[0][0], "one", "nexus_backend_role"),
                            (logins[0][0], "two", "nexus_quant_data_role"),
                        ],
                    )
                current_user = connection.execute("SELECT current_user").fetchone()[0]
                with self.assertRaisesRegex(MigrationError, "administrator"):
                    provision_roles(
                        connection,
                        [(str(current_user), "unsafe", "nexus_backend_role")],
                    )

                actor_one = "v1-" + uuid4().hex + uuid4().hex
                actor_two = "v1-" + uuid4().hex + uuid4().hex
                for actor in (actor_one, actor_two):
                    connection.execute(
                        """
                        INSERT INTO nexus_domain.user_memories
                            (actor_key, content, content_hash)
                        VALUES (%s, 'rls-test', %s)
                        """,
                        (actor, "sha256:" + "1" * 64),
                    )
                connection.commit()
                backend_uri = make_conninfo(
                    **{
                        **conninfo_to_dict(POSTGRES_URI),
                        "user": logins[0][0],
                        "password": logins[0][1],
                    }
                )
                with psycopg.connect(backend_uri) as runtime_connection:
                    self.assertEqual(
                        runtime_connection.execute(
                            "SELECT count(*) FROM nexus_domain.experiments"
                        ).fetchone()[0],
                        0,
                    )
                    runtime_connection.execute(
                        "SELECT set_config('nexus.actor_key', %s, false)",
                        (actor_one,),
                    )
                    self.assertEqual(
                        runtime_connection.execute(
                            "SELECT actor_key FROM nexus_domain.user_memories"
                        ).fetchall(),
                        [(actor_one,)],
                    )
                    runtime_connection.execute(
                        "SELECT set_config('nexus.actor_key', %s, false)",
                        ("system:backend-projector",),
                    )
                    self.assertEqual(
                        runtime_connection.execute(
                            "SELECT count(*) FROM nexus_domain.experiments"
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        runtime_connection.execute(
                            "SELECT nexus_domain.project_product_outbox(60, 10)"
                        ).fetchone()[0],
                        0,
                    )
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        with runtime_connection.transaction():
                            runtime_connection.execute(
                                "SELECT nexus_domain.quant_active_experiment_count()"
                            )
                    runtime_connection.execute(
                        "SELECT set_config('nexus.actor_key', %s, false)",
                        (actor_one,),
                    )
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        with runtime_connection.transaction():
                            runtime_connection.execute(
                                """
                                INSERT INTO nexus_domain.user_memories
                                    (actor_key, content, content_hash)
                                VALUES (%s, 'cross-tenant', %s)
                                """,
                                (actor_two, "sha256:" + "2" * 64),
                            )
                    runtime_connection.execute(
                        "SELECT 1 FROM nexus_domain.experiments LIMIT 1"
                    )
                    runtime_connection.execute(
                        "DELETE FROM nexus_domain.replay_events WHERE false"
                    )
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        with runtime_connection.transaction():
                            runtime_connection.execute(
                                "UPDATE nexus_domain.experiments "
                                "SET status = status WHERE false"
                            )

                quant_worker_uri = make_conninfo(
                    **{
                        **conninfo_to_dict(POSTGRES_URI),
                        "user": logins[2][0],
                        "password": logins[2][1],
                    }
                )
                with psycopg.connect(quant_worker_uri) as runtime_connection:
                    self.assertEqual(
                        runtime_connection.execute(
                            "SELECT nexus_domain.quant_active_experiment_count()"
                        ).fetchone()[0],
                        0,
                    )
                    runtime_connection.execute(
                        "SELECT set_config('nexus.actor_key', %s, false)",
                        ("system:quant-coordinator",),
                    )
                    self.assertEqual(
                        runtime_connection.execute(
                            "SELECT count(*) FROM nexus_domain.experiments"
                        ).fetchone()[0],
                        0,
                    )
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        with runtime_connection.transaction():
                            runtime_connection.execute(
                                "SELECT nexus_domain.project_product_outbox(60, 10)"
                            )
            finally:
                connection.commit()
                with connection.transaction():
                    if "actor_one" in locals():
                        connection.execute(
                            "DELETE FROM nexus_domain.user_memories "
                            "WHERE actor_key IN (%s, %s)",
                            (actor_one, actor_two),
                        )
                    for username, _password, _group in logins:
                        connection.execute(
                            sql.SQL("DROP ROLE IF EXISTS {}").format(
                                sql.Identifier(username)
                            )
                        )


@unittest.skipUnless(POSTGRES_URI, "NEXUS_TEST_POSTGRES_URI is not configured")
class DomainRepositoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The configured database is explicitly disposable. Keep the repository
        # suite self-contained when it is run against a newly created database,
        # including the group roles installed by the production bootstrap command.
        with psycopg.connect(POSTGRES_URI) as connection:
            upgrade(connection)
            provision_roles(connection)

    async def asyncSetUp(self) -> None:
        self.pool = AsyncConnectionPool(
            POSTGRES_URI,
            min_size=1,
            max_size=8,
            open=False,
        )
        await self.pool.open()
        await self.pool.wait()
        self.repository = PostgresDomainControlRepository(self.pool)
        self.actor = "v1-" + uuid4().hex + uuid4().hex

    async def asyncTearDown(self) -> None:
        await self.pool.close()

    async def test_concurrent_reservation_conflict_tenant_scope_and_atomic_outbox(self):
        digest = "sha256:" + "1" * 64
        reservations = await asyncio.gather(
            *(
                self.repository.reserve_idempotency(
                    actor_key=self.actor,
                    operation="experiment.submit",
                    idempotency_key="retry-key",
                    arguments_digest=digest,
                )
                for _ in range(8)
            )
        )
        self.assertEqual(sum(created for _decision, created in reservations), 1)
        decision = reservations[0][0]
        with self.assertRaises(IdempotencyConflict):
            await self.repository.reserve_idempotency(
                actor_key=self.actor,
                operation="experiment.submit",
                idempotency_key="retry-key",
                arguments_digest="sha256:" + "2" * 64,
            )

        other_actor = "v1-" + uuid4().hex + uuid4().hex
        _other, created = await self.repository.reserve_idempotency(
            actor_key=other_actor,
            operation="experiment.submit",
            idempotency_key="retry-key",
            arguments_digest=digest,
        )
        self.assertTrue(created)

        event = await self.repository.complete_idempotency_with_event(
            decision=decision,
            result_reference={"experiment_id": "exp_123"},
            thread_id="thread-1",
            run_id="run-1",
            event_type="worker.accepted",
            payload={
                "worker": "quant-worker",
                "job_type": "qlib_experiment",
                "job_id": "exp_123",
                "status": "queued",
            },
        )
        repeated = await self.repository.complete_idempotency_with_event(
            decision=decision,
            result_reference={"experiment_id": "exp_123"},
            thread_id="thread-1",
            run_id="run-1",
            event_type="worker.accepted",
            payload={
                "worker": "quant-worker",
                "job_type": "qlib_experiment",
                "job_id": "exp_123",
                "status": "queued",
            },
        )
        self.assertEqual(repeated.event_id, event.event_id)
        async with self.pool.connection() as connection:
            events = await (
                await connection.execute(
                    "SELECT count(*) FROM nexus_domain.product_events "
                    "WHERE actor_key = %s",
                    (self.actor,),
                )
            ).fetchone()
            outbox = await (
                await connection.execute(
                    "SELECT count(*) FROM nexus_domain.transactional_outbox "
                    "WHERE actor_key = %s",
                    (self.actor,),
                )
            ).fetchone()
        self.assertEqual(events[0], 1)
        self.assertEqual(outbox[0], 1)

    async def test_authorization_lease_is_actor_thread_scoped_replaceable_and_revocable(self):
        repository = PostgresAuthorizationLeaseRepository(self.pool)
        first = await repository.create(
            actor_key=self.actor,
            thread_id="thread-permission",
            mode="autonomous",
            ttl_seconds=900,
        )
        loaded = await repository.get_active(
            actor_key=self.actor,
            thread_id="thread-permission",
        )
        replacement = await repository.create(
            actor_key=self.actor,
            thread_id="thread-permission",
            mode="full_access",
            ttl_seconds=3600,
            allow_sensitive=True,
        )
        other_actor = "v1-" + uuid4().hex + uuid4().hex

        self.assertEqual(loaded, first)
        self.assertNotEqual(first.lease_id, replacement.lease_id)
        self.assertTrue(replacement.allows("delete_user_memory"))
        self.assertIsNone(
            await repository.get_active(
                actor_key=other_actor,
                thread_id="thread-permission",
            )
        )
        arguments = normalize_governed_arguments(
            "save_user_memory",
            {
                "memory_key": "preference.permission_test",
                "content": "bounded",
            },
            tool_call_id="call-authorization-lease",
        )
        target, digest = approval_action_identity(
            actor_key=self.actor,
            tool_name="save_user_memory",
            normalized_arguments=arguments,
        )
        approval, _created = await PostgresApprovalRepository(self.pool).request(
            actor_key=self.actor,
            thread_id="thread-permission",
            run_id="run-authorization-lease",
            action_digest=digest,
            tool_name="save_user_memory",
            target_boundary=target,
            normalized_arguments=arguments,
            tool_call_id="call-authorization-lease",
        )
        await PostgresApprovalRepository(self.pool).decide(
            approval,
            decision="approve",
            source=f"authorization-lease:{replacement.lease_id}",
        )
        self.assertTrue(
            await repository.revoke(
                actor_key=self.actor,
                lease_id=replacement.lease_id,
            )
        )
        self.assertIsNone(
            await repository.get_active(
                actor_key=self.actor,
                thread_id="thread-permission",
            )
        )
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT lease_id, revocation_reason
                    FROM nexus_domain.authorization_leases
                    WHERE actor_key = %s AND thread_id = %s
                    ORDER BY created_at
                    """,
                    (self.actor, "thread-permission"),
                )
            ).fetchall()
            audit = await (
                await connection.execute(
                    """
                    SELECT a.decision_metadata->>'source',
                           e.payload->>'decision_source'
                    FROM nexus_domain.approval_requests a
                    JOIN nexus_domain.product_events e
                      ON e.actor_key = a.actor_key AND e.run_id = a.run_id
                    WHERE a.actor_key = %s
                      AND a.approval_request_id = %s
                      AND e.event_type = 'approval.decided'
                    """,
                    (self.actor, approval.approval_request_id),
                )
            ).fetchone()
        self.assertEqual(
            rows,
            [
                (first.lease_id, "replaced"),
                (replacement.lease_id, "user_revoked"),
            ],
        )
        self.assertEqual(
            audit,
            (
                f"authorization-lease:{replacement.lease_id}",
                "authorization_lease",
            ),
        )

    async def test_user_memory_preserves_unrelated_facts_and_tenant_scope(self):
        repository = PostgresUserMemoryRepository(self.pool)
        first = await repository.save(
            self.actor,
            "  Preferred name is Example User.  ",
            memory_key="identity.name",
        )
        repeated = await repository.save(
            self.actor,
            "Preferred name is Example User.",
            memory_key="identity.name",
        )
        updated = await PostgresUserMemoryRepository(self.pool).save(
            self.actor,
            "Enjoys a multiplayer game.",
            memory_key="preference.game.example",
        )
        other_actor = "v1-" + uuid4().hex + uuid4().hex

        self.assertTrue(first.changed)
        self.assertEqual(first.memory.revision, 1)
        self.assertFalse(repeated.changed)
        self.assertEqual(repeated.memory.revision, 1)
        self.assertTrue(updated.changed)
        self.assertEqual(updated.memory.revision, 2)
        aggregate = (
            await PostgresUserMemoryRepository(self.pool).get(self.actor)
        ).content
        self.assertIn("[identity.name]\nPreferred name is Example User.", aggregate)
        self.assertIn(
            "[preference.game.example]\nEnjoys a multiplayer game.",
            aggregate,
        )
        async with self.pool.connection() as connection:
            item_count = await (
                await connection.execute(
                    """
                    SELECT count(*) FROM nexus_domain.user_memory_items
                    WHERE actor_key = %s
                    """,
                    (self.actor,),
                )
            ).fetchone()
        self.assertEqual(item_count[0], 2)
        self.assertIsNone(await repository.get(other_actor))
        self.assertTrue(await repository.delete(self.actor))
        self.assertFalse(await repository.delete(self.actor))
        self.assertIsNone(await repository.get(self.actor))
        async with self.pool.connection() as connection:
            remaining_items = await (
                await connection.execute(
                    """
                    SELECT count(*) FROM nexus_domain.user_memory_items
                    WHERE actor_key = %s
                    """,
                    (self.actor,),
                )
            ).fetchone()
        self.assertEqual(remaining_items[0], 0)

    async def test_outbox_failure_rolls_back_domain_state_and_event(self) -> None:
        decision, _created = await self.repository.reserve_idempotency(
            actor_key=self.actor,
            operation="dataset.build",
            idempotency_key="atomic-key",
            arguments_digest="sha256:" + "3" * 64,
        )
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                CREATE OR REPLACE FUNCTION nexus_domain.reject_test_outbox()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'test outbox rejection';
                END
                $$
                """
            )
            await connection.execute(
                """
                CREATE TRIGGER reject_test_outbox
                BEFORE INSERT ON nexus_domain.transactional_outbox
                FOR EACH ROW EXECUTE FUNCTION nexus_domain.reject_test_outbox()
                """
            )
        try:
            with self.assertRaises(psycopg.Error):
                await self.repository.complete_idempotency_with_event(
                    decision=decision,
                    result_reference={"dataset_revision_id": "dsr_v1_test"},
                    thread_id=None,
                    run_id="run-atomic",
                    event_type="dataset.ready",
                    payload={
                        "dataset_revision_id": "dsr_v1_test",
                        "manifest_hash": "sha256:" + "3" * 64,
                        "dataset_alias": "atomic-test",
                    },
                )
            async with self.pool.connection() as connection:
                state = await (
                    await connection.execute(
                        "SELECT state FROM nexus_domain.idempotency_decisions "
                        "WHERE actor_key = %s AND operation = 'dataset.build'",
                        (self.actor,),
                    )
                ).fetchone()
                events = await (
                    await connection.execute(
                        "SELECT count(*) FROM nexus_domain.product_events "
                        "WHERE actor_key = %s",
                        (self.actor,),
                    )
                ).fetchone()
            self.assertEqual(state[0], "reserved")
            self.assertEqual(events[0], 0)
        finally:
            async with self.pool.connection() as connection:
                await connection.execute(
                    "DROP TRIGGER IF EXISTS reject_test_outbox "
                    "ON nexus_domain.transactional_outbox"
                )
                await connection.execute(
                    "DROP FUNCTION IF EXISTS nexus_domain.reject_test_outbox()"
                )

    async def test_worker_roles_cannot_access_checkpoint_or_other_private_tables(self):
        async with self.pool.connection() as connection:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS langgraph.role_probe (id integer)"
            )
            quant_data_checkpoint = await (
                await connection.execute(
                    "SELECT has_table_privilege("
                    "'nexus_quant_data_role', 'langgraph.role_probe', 'SELECT')"
                )
            ).fetchone()
            quant_data_experiments = await (
                await connection.execute(
                    "SELECT has_table_privilege("
                    "'nexus_quant_data_role', 'nexus_domain.experiments', 'SELECT')"
                )
            ).fetchone()
            quant_worker_staging = await (
                await connection.execute(
                    "SELECT has_table_privilege("
                    "'nexus_quant_worker_role', "
                    "'nexus_domain.staging_revisions', 'SELECT')"
                )
            ).fetchone()
            backend_evidence = await (
                await connection.execute(
                    """
                    SELECT
                      has_table_privilege(
                        'nexus_backend_role',
                        'nexus_domain.dataset_revisions', 'SELECT'
                      ),
                      has_table_privilege(
                        'nexus_backend_role',
                        'nexus_domain.dataset_manifest_files', 'SELECT'
                      ),
                      has_table_privilege(
                        'nexus_backend_role',
                        'nexus_domain.experiments', 'SELECT'
                      ),
                      has_table_privilege(
                        'nexus_backend_role',
                        'nexus_domain.job_attempts', 'SELECT'
                      ),
                      has_table_privilege(
                        'nexus_backend_role',
                        'nexus_domain.experiments', 'UPDATE'
                      ),
                      has_table_privilege(
                        'nexus_backend_role',
                        'nexus_domain.replay_events', 'DELETE'
                      )
                    """
                )
            ).fetchone()
        self.assertFalse(quant_data_checkpoint[0])
        self.assertFalse(quant_data_experiments[0])
        self.assertFalse(quant_worker_staging[0])
        self.assertEqual(backend_evidence, (True, True, True, True, False, True))

    async def test_workflow_transition_event_and_outbox_are_one_durable_sequence(self):
        repository = PostgresWorkflowRepository(self.pool)
        digest = "act_v1_" + "4" * 64
        record, created = await repository.start(
            actor_key=self.actor,
            workflow_type="prepare_qlib_dataset",
            idempotency_key="workflow-key",
            intent_digest=digest,
            normalized_input={"symbols": ["AAPL"]},
        )
        resumed, created_again = await repository.start(
            actor_key=self.actor,
            workflow_type="prepare_qlib_dataset",
            idempotency_key="workflow-key",
            intent_digest=digest,
            normalized_input={"symbols": ["AAPL"]},
        )
        transitioned = await repository.transition(
            record,
            allowed_from={"requested"},
            to_state="staged",
            stage="fetch_or_resolve_staging_revision",
            output_patch={"staging_revision_id": "stg_v1_" + "5" * 24},
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(resumed.workflow_id, record.workflow_id)
        self.assertEqual(transitioned.state, "staged")
        with self.assertRaises(WorkflowConflict):
            await repository.start(
                actor_key=self.actor,
                workflow_type="prepare_qlib_dataset",
                idempotency_key="workflow-key",
                intent_digest="act_v1_" + "6" * 64,
                normalized_input={"symbols": ["MSFT"]},
            )
        async with self.pool.connection() as connection:
            counts = await (
                await connection.execute(
                    """
                    SELECT
                      (SELECT count(*) FROM nexus_domain.workflow_transitions
                       WHERE actor_key = %s AND workflow_id = %s),
                      (SELECT count(*) FROM nexus_domain.product_events
                       WHERE actor_key = %s AND run_id = %s),
                      (SELECT count(*) FROM nexus_domain.transactional_outbox o
                       JOIN nexus_domain.product_events e
                         ON e.actor_key = o.actor_key AND e.event_id = o.event_id
                       WHERE e.actor_key = %s AND e.run_id = %s)
                    """,
                    (
                        self.actor,
                        record.workflow_id,
                        self.actor,
                        record.workflow_id,
                        self.actor,
                        record.workflow_id,
                    ),
                )
            ).fetchone()
        self.assertEqual(counts, (2, 2, 2))

    async def test_interpretation_dossier_export_and_dataset_evidence_immutability(self):
        request_id = "rqf_v1_" + uuid4().hex + uuid4().hex
        staging_id = "stg_v1_" + uuid4().hex[:24]
        dataset_id = "dsr_v1_" + uuid4().hex[:24]
        experiment_id = "exp_v1_" + uuid4().hex
        metric_id = "lin_v1_" + uuid4().hex
        artifact_id = "art_v1_" + uuid4().hex[:24]
        specification = {
            "dataset_id": dataset_id,
            "universe": "evidence_universe",
            "feature_set": "Alpha158",
            "model": "linear",
            "train": {"start": "2020-01-01", "end": "2020-01-03"},
            "valid": {"start": "2020-01-04", "end": "2020-01-05"},
            "test": {"start": "2020-01-06", "end": "2020-01-08"},
            "strategy": {"type": "topk_dropout"},
        }
        limitations = {
            "production_eligibility": "research_only",
            "lookahead": {"label_lookahead_trading_days": 2},
        }
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.request_fingerprints
                        (actor_key, request_fingerprint, namespace,
                         schema_version, normalized_request)
                    VALUES (%s, %s, 'test', '1', '{}'::jsonb)
                    """,
                    (self.actor, request_id),
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.staging_revisions
                        (actor_key, staging_revision_id, request_fingerprint,
                         content_hash, schema_version, metadata, status)
                    VALUES (%s, %s, %s, %s, '1', '{}'::jsonb, 'ready')
                    """,
                    (self.actor, staging_id, request_id, "sha256:" + "1" * 64),
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.dataset_revisions
                        (actor_key, dataset_revision_id, source_staging_revision_id,
                         manifest_hash, schema_version, status, limitations, ready_at)
                    VALUES (%s, %s, %s, %s, '1', 'ready', %s, now())
                    """,
                    (
                        self.actor,
                        dataset_id,
                        staging_id,
                        "sha256:" + "2" * 64,
                        Jsonb(limitations),
                    ),
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.dataset_manifest_files
                        (actor_key, dataset_revision_id, relative_path,
                         media_type, size_bytes, content_hash)
                    VALUES (%s, %s, 'calendars/day.txt', 'text/plain', 10, %s)
                    """,
                    (self.actor, dataset_id, "sha256:" + "3" * 64),
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.experiments
                        (actor_key, experiment_id, dataset_revision_id,
                         specification_digest, specification, status,
                         idempotency_key, arguments_digest, result)
                    VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s, %s)
                    """,
                    (
                        self.actor,
                        experiment_id,
                        dataset_id,
                        "sha256:" + "4" * 64,
                        Jsonb(specification),
                        "interpretation-test",
                        "act_v1_" + "5" * 64,
                        Jsonb({"signal_metrics": {"ic": 0.08}, "limitations": limitations}),
                    ),
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.job_attempts
                        (actor_key, experiment_id, attempt, status, stage,
                         started_at, finished_at, runtime_versions)
                    VALUES (%s, %s, 1, 'completed', 'completed', now(), now(), %s)
                    """,
                    (self.actor, experiment_id, Jsonb({"python": "3.11-test"})),
                )
                await connection.execute(
                    """
                    INSERT INTO nexus_domain.artifacts
                        (actor_key, artifact_id, content_hash, media_type,
                         size_bytes, storage_key, manifest)
                    VALUES (%s, %s, %s, 'application/vnd.apache.parquet',
                            10, 'internal/test', %s)
                    """,
                    (
                        self.actor,
                        artifact_id,
                        "sha256:" + "6" * 64,
                        Jsonb(
                            {
                                "artifact_id": artifact_id,
                                "producer_id": experiment_id,
                                "storage_status": "ready",
                            }
                        ),
                    ),
                )
                for node_id, node_type, properties in (
                    (dataset_id, "dataset_revision", {"dataset_revision_id": dataset_id}),
                    (experiment_id, "experiment", {"experiment_id": experiment_id}),
                    (artifact_id, "artifact", {"artifact_id": artifact_id}),
                    (
                        metric_id,
                        "metric",
                        {
                            "experiment_id": experiment_id,
                            "metric_group": "signal_metrics",
                            "name": "ic",
                            "value": 0.08,
                            "meaning": "Mean correlation.",
                            "calculation_version": "qlib-result-v1",
                            "dataset_revision_id": dataset_id,
                            "specification_digest": "sha256:" + "4" * 64,
                            "attempt_id": f"{experiment_id}:attempt:1",
                            "artifact_ids": [artifact_id],
                        },
                    ),
                ):
                    await connection.execute(
                        """
                        INSERT INTO nexus_domain.lineage_nodes
                            (actor_key, node_id, node_type, schema_version, properties)
                        VALUES (%s, %s, %s, '1', %s)
                        """,
                        (self.actor, node_id, node_type, Jsonb(properties)),
                    )

        repository = PostgresInterpretationRepository(self.pool)
        values = {
            "actor_key": self.actor,
            "experiment_id": experiment_id,
            "structured_content": {
                "summary": "Evidence-backed research interpretation.",
                "findings": [{"metric_id": metric_id, "statement": "Positive IC."}],
            },
            "evidence_references": [metric_id, artifact_id],
            "idempotency_key": "interpretation-idempotency",
            "thread_id": "thread-interpretation",
            "run_id": "run-interpretation",
        }
        report = await repository.record(**values)
        repeated = await repository.record(**values)
        with self.assertRaises(InterpretationConflict):
            await repository.record(
                **{
                    **values,
                    "actor_key": "v1-" + uuid4().hex + uuid4().hex,
                }
            )

        lineage = PostgresLineageRepository(self.pool)
        dossier = await lineage.experiment_dossier(
            actor_key=self.actor,
            experiment_id=experiment_id,
        )
        hidden = await lineage.experiment_dossier(
            actor_key="v1-" + uuid4().hex + uuid4().hex,
            experiment_id=experiment_id,
        )
        exported = await lineage.export_dossier(
            actor_key=self.actor,
            experiment_id=experiment_id,
        )
        catalog = await PostgresExperimentCatalogRepository(self.pool).list_experiments(
            actor_key=self.actor,
            limit=1,
            statuses=("completed",),
        )
        hidden_catalog = await PostgresExperimentCatalogRepository(
            self.pool
        ).list_experiments(
            actor_key="v1-" + uuid4().hex + uuid4().hex,
            limit=1,
        )

        self.assertEqual(repeated["interpretation_report_id"], report["interpretation_report_id"])
        self.assertTrue(repeated["reused"])
        self.assertIsNone(hidden)
        self.assertEqual(dossier["metrics"][0]["evidence_status"], "complete")
        self.assertEqual(
            dossier["interpretations"][0]["interpretation_report_id"],
            report["interpretation_report_id"],
        )
        self.assertIn(metric_id, exported["included_evidence_ids"])
        self.assertNotIn("storage_key", str(exported))
        self.assertEqual(catalog["items"][0]["experiment_id"], experiment_id)
        self.assertEqual(catalog["items"][0]["specification_summary"]["model"], "linear")
        self.assertEqual(hidden_catalog["items"], [])

        async with self.pool.connection() as connection:
            with self.assertRaises(psycopg.Error):
                async with connection.transaction():
                    await connection.execute(
                        """
                        UPDATE nexus_domain.dataset_revisions
                        SET limitations = '{"production_eligibility":"eligible"}'::jsonb
                        WHERE actor_key = %s AND dataset_revision_id = %s
                        """,
                        (self.actor, dataset_id),
                    )
            for operation in (
                """
                INSERT INTO nexus_domain.dataset_manifest_files
                    (actor_key, dataset_revision_id, relative_path,
                     media_type, size_bytes, content_hash)
                VALUES (%s, %s, 'features/late.bin',
                        'application/octet-stream', 1, %s)
                """,
                """
                UPDATE nexus_domain.dataset_manifest_files
                SET content_hash = %s
                WHERE actor_key = %s AND dataset_revision_id = %s
                """,
                """
                DELETE FROM nexus_domain.dataset_manifest_files
                WHERE actor_key = %s AND dataset_revision_id = %s
                """,
            ):
                parameters = (
                    (self.actor, dataset_id, "sha256:" + "7" * 64)
                    if operation.lstrip().startswith("INSERT")
                    else ("sha256:" + "8" * 64, self.actor, dataset_id)
                    if operation.lstrip().startswith("UPDATE")
                    else (self.actor, dataset_id)
                )
                with self.assertRaises(psycopg.Error):
                    async with connection.transaction():
                        await connection.execute(operation, parameters)

    async def test_publication_effect_is_actor_scoped_idempotent_and_audited(self):
        repository = PostgresPublicationRepository(self.pool)
        approval_repository = PostgresApprovalRepository(self.pool)
        arguments = {
            "market": "us-stock",
            "title": "Evidence",
            "content": "Research only.",
            "evidence_ids": [
                "exp_v1_" + "7" * 32,
                "src_v1_" + "8" * 32,
            ],
        }
        target, action_digest = approval_action_identity(
            actor_key=self.actor,
            tool_name="publish_ai_trader_strategy",
            normalized_arguments=arguments,
        )
        approval, _created = await approval_repository.request(
            actor_key=self.actor,
            thread_id="thread-publication",
            run_id="run-publication",
            action_digest=action_digest,
            tool_name="publish_ai_trader_strategy",
            target_boundary=target,
            normalized_arguments=arguments,
        )
        await approval_repository.decide(approval, decision="approve")
        values = {
            "actor_key": self.actor,
            "publication_type": "strategy",
            "tool_name": "publish_ai_trader_strategy",
            "target_boundary": target,
            "normalized_arguments": arguments,
            "action_digest": action_digest,
            "idempotency_key": "publication-key",
            "thread_id": "thread-publication",
            "run_id": "run-publication",
        }
        effect, created = await repository.reserve_approved_effect(**values)
        same, created_again = await repository.reserve_approved_effect(**values)
        published = await repository.mark_published(
            effect,
            thread_id="thread-publication",
            run_id="run-publication",
            target_reference={"success": True, "signal_id": 42},
        )
        repeated = await repository.mark_published(
            same,
            thread_id="thread-publication",
            run_id="run-publication",
            target_reference={"success": True, "signal_id": 42},
        )
        same_action, new_key_created = await repository.reserve_approved_effect(
            **{**values, "idempotency_key": "different-retry-key"}
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(published.status, "published")
        self.assertEqual(repeated.target_reference["signal_id"], 42)
        self.assertFalse(new_key_created)
        self.assertEqual(same_action.publication_id, effect.publication_id)
        self.assertEqual(same_action.idempotency_key, "publication-key")
        changed_arguments = {**arguments, "content": "Changed."}
        changed_target, changed_digest = approval_action_identity(
            actor_key=self.actor,
            tool_name="publish_ai_trader_strategy",
            normalized_arguments=changed_arguments,
        )
        changed_approval, _created = await approval_repository.request(
            actor_key=self.actor,
            thread_id="thread-publication",
            run_id="run-publication-changed",
            action_digest=changed_digest,
            tool_name="publish_ai_trader_strategy",
            target_boundary=changed_target,
            normalized_arguments=changed_arguments,
        )
        await approval_repository.decide(changed_approval, decision="approve")
        with self.assertRaises(PublicationConflict):
            await repository.reserve_approved_effect(
                **{
                    **values,
                    "action_digest": changed_digest,
                    "normalized_arguments": changed_arguments,
                }
            )
        async with self.pool.connection() as connection:
            counts = await (
                await connection.execute(
                    """
                    SELECT
                      (SELECT count(*) FROM nexus_domain.approval_requests
                       WHERE actor_key = %s),
                      (SELECT count(*) FROM nexus_domain.publication_effects
                       WHERE actor_key = %s),
                      (SELECT count(*) FROM nexus_domain.product_events
                       WHERE actor_key = %s
                         AND event_type = 'publication.published'),
                      (SELECT count(*) FROM nexus_domain.transactional_outbox o
                       JOIN nexus_domain.product_events e
                         ON e.actor_key = o.actor_key AND e.event_id = o.event_id
                       WHERE e.actor_key = %s
                         AND e.event_type = 'publication.published')
                    """,
                    (self.actor, self.actor, self.actor, self.actor),
                )
            ).fetchone()
            evidence = await (
                await connection.execute(
                    """
                    SELECT a.execution_status, a.executed_at IS NOT NULL,
                           a.human_summary, p.thread_id, p.run_id,
                           p.evidence_references
                    FROM nexus_domain.approval_requests a
                    JOIN nexus_domain.publication_effects p
                      ON p.actor_key = a.actor_key
                     AND p.approval_request_id = a.approval_request_id
                    WHERE p.actor_key = %s AND p.publication_id = %s
                    """,
                    (self.actor, effect.publication_id),
                )
            ).fetchone()
        self.assertEqual(counts, (2, 1, 1, 1))
        self.assertEqual(evidence[0:2], ("executed", True))
        self.assertNotIn("Research only.", str(evidence[2]))
        self.assertEqual(evidence[3:5], ("thread-publication", "run-publication"))
        self.assertEqual(evidence[5], arguments["evidence_ids"])
    async def test_rejected_approval_reserves_publication_key_and_cannot_publish(self):
        approvals = PostgresApprovalRepository(self.pool)
        publications = PostgresPublicationRepository(self.pool)
        arguments = {
            "market": "crypto",
            "title": "Rejected",
            "content": "Do not publish.",
            "symbol": "BTC",
            "tags": None,
        }
        target, digest = approval_action_identity(
            actor_key=self.actor,
            tool_name="publish_ai_trader_discussion",
            normalized_arguments=arguments,
        )
        approval, _created = await approvals.request(
            actor_key=self.actor,
            thread_id="thread-rejected",
            run_id="run-rejected",
            action_digest=digest,
            tool_name="publish_ai_trader_discussion",
            target_boundary=target,
            normalized_arguments=arguments,
        )
        await approvals.decide(
            approval,
            decision="reject",
            idempotency_key="rejected-publication-key",
        )

        with self.assertRaises(PublicationConflict):
            await publications.reserve_approved_effect(
                actor_key=self.actor,
                publication_type="discussion",
                tool_name="publish_ai_trader_discussion",
                target_boundary=target,
                normalized_arguments=arguments,
                action_digest=digest,
                idempotency_key="rejected-publication-key",
                thread_id="thread-rejected",
                run_id="run-rejected",
            )
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT status FROM nexus_domain.publication_effects
                    WHERE actor_key = %s AND idempotency_key = %s
                    """,
                    (self.actor, "rejected-publication-key"),
                )
            ).fetchone()
        self.assertEqual(row[0], "rejected")

    async def test_expired_approval_is_audited_and_requires_a_new_request(self):
        approvals = PostgresApprovalRepository(
            self.pool,
            approval_ttl_seconds=60,
        )
        arguments = normalize_governed_arguments(
            "save_user_memory",
            {"memory_key": "test.expiry", "content": "bounded"},
        )
        target, digest = approval_action_identity(
            actor_key=self.actor,
            tool_name="save_user_memory",
            normalized_arguments=arguments,
        )
        approval, created = await approvals.request(
            actor_key=self.actor,
            thread_id="thread-expiry",
            run_id="run-expiry",
            action_digest=digest,
            tool_name="save_user_memory",
            target_boundary=target,
            normalized_arguments=arguments,
        )
        async with self.pool.connection() as connection:
            stored = await (
                await connection.execute(
                    """
                    SELECT normalized_arguments::text, human_summary::text
                    FROM nexus_domain.approval_requests
                    WHERE actor_key = %s AND approval_request_id = %s
                    """,
                    (self.actor, approval.approval_request_id),
                )
            ).fetchone()
            self.assertNotIn("bounded", stored[0])
            self.assertNotIn("bounded", stored[1])
            await connection.execute(
                """
                UPDATE nexus_domain.approval_requests
                SET expires_at = now() - interval '1 second'
                WHERE actor_key = %s AND approval_request_id = %s
                """,
                (self.actor, approval.approval_request_id),
            )

        with self.assertRaisesRegex(ApprovalConflict, "approval_request_expired"):
            await approvals.decide(approval, decision="approve")

        replacement, replacement_created = await approvals.request(
            actor_key=self.actor,
            thread_id="thread-expiry",
            run_id="run-expiry",
            action_digest=digest,
            tool_name="save_user_memory",
            target_boundary=target,
            normalized_arguments=arguments,
        )
        with self.assertRaisesRegex(ApprovalConflict, "action_digest_conflict"):
            await approvals.request(
                actor_key=self.actor,
                thread_id="another-thread",
                run_id="another-run",
                action_digest=digest,
                tool_name="save_user_memory",
                target_boundary=target,
                normalized_arguments=arguments,
            )

        self.assertTrue(created)
        self.assertTrue(replacement_created)
        self.assertNotEqual(
            replacement.approval_request_id,
            approval.approval_request_id,
        )
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT status FROM nexus_domain.approval_requests
                    WHERE actor_key = %s AND action_digest = %s
                    ORDER BY requested_at
                    """,
                    (self.actor, digest),
                )
            ).fetchall()
            expiry_events = await (
                await connection.execute(
                    """
                    SELECT count(*) FROM nexus_domain.product_events
                    WHERE actor_key = %s AND run_id = 'run-expiry'
                      AND event_type = 'approval.expired'
                    """,
                    (self.actor,),
                )
            ).fetchone()
        self.assertEqual([row[0] for row in rows], ["expired", "pending"])
        self.assertEqual(expiry_events[0], 1)

    async def test_pending_approval_resumes_across_runs_for_the_same_tool_call(self):
        approvals = PostgresApprovalRepository(self.pool)
        arguments = {
            "market": "us-stock",
            "title": "Cross-run approval",
            "content": "Exact governed action.",
            "symbols": "AAPL",
            "tags": None,
        }
        target, digest = approval_action_identity(
            actor_key=self.actor,
            tool_name="publish_ai_trader_strategy",
            normalized_arguments=arguments,
        )
        proposed, created = await approvals.request(
            actor_key=self.actor,
            thread_id="thread-cross-run",
            run_id="run-proposal",
            action_digest=digest,
            tool_name="publish_ai_trader_strategy",
            target_boundary=target,
            normalized_arguments=arguments,
            tool_call_id="call-cross-run",
        )
        resumed, resumed_created = await approvals.request(
            actor_key=self.actor,
            thread_id="thread-cross-run",
            run_id="run-resume",
            action_digest=digest,
            tool_name="publish_ai_trader_strategy",
            target_boundary=target,
            normalized_arguments=arguments,
            tool_call_id="call-cross-run",
        )

        with self.assertRaisesRegex(ApprovalConflict, "action_digest_conflict"):
            await approvals.request(
                actor_key=self.actor,
                thread_id="thread-cross-run",
                run_id="run-unrelated",
                action_digest=digest,
                tool_name="publish_ai_trader_strategy",
                target_boundary=target,
                normalized_arguments=arguments,
                tool_call_id="call-unrelated",
            )

        self.assertTrue(created)
        self.assertFalse(resumed_created)
        self.assertEqual(resumed.approval_request_id, proposed.approval_request_id)
        self.assertEqual(resumed.run_id, "run-proposal")

    async def test_outbox_projection_replays_in_order_and_enforces_actor_scope(self):
        repository = PostgresProductEventRepository(
            self.pool,
            replay_retention_seconds=3600,
        )
        first = await repository.append(
            actor_key=self.actor,
            thread_id="thread-replay",
            run_id="run-replay",
            event_type="run.started",
            payload={"graph_name": "supervisor"},
        )
        second = await repository.append(
            actor_key=self.actor,
            thread_id="thread-replay",
            run_id="run-replay",
            event_type="run.completed",
            payload={"graph_name": "supervisor"},
        )

        self.assertGreaterEqual(await repository.project_outbox(), 2)
        self.assertEqual(await repository.project_outbox(), 0)
        page = await repository.fetch_after(
            actor_key=self.actor,
            run_id="run-replay",
            after=0,
        )
        other = await repository.fetch_after(
            actor_key="v1-" + uuid4().hex + uuid4().hex,
            run_id="run-replay",
            after=0,
        )

        self.assertEqual(
            [item["event_id"] for item in page["events"]],
            [first["event_id"], second["event_id"]],
        )
        self.assertEqual(page["last_sequence"], second["sequence"])
        self.assertFalse(page["gap"])
        self.assertEqual(other["events"], [])

        async with self.pool.connection() as connection:
            await connection.execute(
                """
                DELETE FROM nexus_domain.replay_events
                WHERE actor_key = %s AND run_id = 'run-replay' AND sequence = %s
                """,
                (self.actor, first["sequence"]),
            )
        gap = await repository.fetch_after(
            actor_key=self.actor,
            run_id="run-replay",
            after=0,
        )
        self.assertTrue(gap["gap"])
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                DELETE FROM nexus_domain.replay_events
                WHERE actor_key = %s AND run_id = 'run-replay'
                """,
                (self.actor,),
            )
        fully_expired = await repository.fetch_after(
            actor_key=self.actor,
            run_id="run-replay",
            after=0,
        )
        self.assertTrue(fully_expired["gap"])
        self.assertEqual(fully_expired["events"], [])

    async def test_research_sources_are_revisioned_linked_and_actor_scoped(self):
        run_id = "run-source-evidence"
        repository = PostgresResearchSourceRepository(self.pool)
        results = [
            {
                "url": "HTTPS://Example.COM/report?q=alpha#private",
                "title": "Market report",
                "content": "First bounded excerpt token=private-value",
                "published_date": "2026-07-14",
            }
        ]
        first = await repository.record_search_results(
            actor_key=self.actor,
            thread_id="thread-source-evidence",
            run_id=run_id,
            query="alpha",
            results=results,
        )
        repeated = await repository.record_search_results(
            actor_key=self.actor,
            thread_id="thread-source-evidence",
            run_id=run_id,
            query="alpha",
            results=results,
        )
        revised = await repository.record_search_results(
            actor_key=self.actor,
            thread_id="thread-source-evidence",
            run_id=run_id,
            query="alpha",
            results=[{**results[0], "content": "Revised excerpt"}],
        )
        graph = await PostgresLineageRepository(self.pool).graph(
            actor_key=self.actor,
            node_id=run_id,
            direction="forward",
            depth=2,
            limit=20,
        )
        hidden_graph = await PostgresLineageRepository(self.pool).graph(
            actor_key="v1-" + uuid4().hex + uuid4().hex,
            node_id=run_id,
        )

        self.assertEqual(first[0]["source_record_id"], repeated[0]["source_record_id"])
        self.assertNotEqual(first[0]["source_record_id"], revised[0]["source_record_id"])
        self.assertIsNotNone(graph)
        self.assertEqual(
            {node["node_type"] for node in graph["nodes"]},
            {"research_run", "research_source"},
        )
        self.assertEqual(
            {edge["edge_type"] for edge in graph["edges"]},
            {"cites_source"},
        )
        self.assertIsNone(hidden_graph)


if __name__ == "__main__":
    unittest.main()
