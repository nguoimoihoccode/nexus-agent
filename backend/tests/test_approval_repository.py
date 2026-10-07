import unittest

from source.domain import ApprovalConflict
from source.infrastructure.approval_repository import PostgresApprovalRepository


ACTOR = "v1-" + "a" * 64
DIGEST = "act_v1_" + "b" * 64


class _Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Connection:
    def __init__(self, rows):
        self.rows = iter(rows)

    @staticmethod
    def transaction():
        return _Transaction()

    async def execute(self, _query, _parameters):
        return _Cursor(next(self.rows))


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


class ApprovalRepositoryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _stored_row(*, tool_call_id="call-memory-1"):
        return (
            "apr_v1_" + "1" * 32,
            "thread-memory",
            "run-proposal",
            DIGEST,
            "save_user_memory",
            "postgres:user-memory",
            {
                "memory_key": "preference.response_style",
                "content_hash": "sha256:" + "2" * 64,
                "content_bytes": 12,
            },
            "pending",
            tool_call_id,
        )

    async def _request(self, *, stored_tool_call_id="call-memory-1"):
        repository = PostgresApprovalRepository(
            _Pool([None, self._stored_row(tool_call_id=stored_tool_call_id)])
        )
        return await repository.request(
            actor_key=ACTOR,
            thread_id="thread-memory",
            run_id="run-resume",
            action_digest=DIGEST,
            tool_name="save_user_memory",
            target_boundary="postgres:user-memory",
            normalized_arguments={
                "memory_key": "preference.response_style",
                "content_hash": "sha256:" + "2" * 64,
                "content_bytes": 12,
            },
            tool_call_id="call-memory-1",
        )

    async def test_exact_pending_tool_call_can_resume_in_a_new_run(self):
        approval, created = await self._request()

        self.assertFalse(created)
        self.assertEqual(approval.run_id, "run-proposal")
        self.assertEqual(approval.status, "pending")

    async def test_new_run_cannot_reuse_a_different_tool_call(self):
        with self.assertRaisesRegex(ApprovalConflict, "action_digest_conflict"):
            await self._request(stored_tool_call_id="another-call")


if __name__ == "__main__":
    unittest.main()
