import unittest
from datetime import datetime, timedelta, timezone

from source.domain import AuthorizationLease


ACTOR = "v1-" + "a" * 64


def lease(*, mode="autonomous", allow_sensitive=False, expires_in=3600):
    now = datetime.now(timezone.utc)
    return AuthorizationLease(
        actor_key=ACTOR,
        lease_id="azl_v1_" + "1" * 32,
        thread_id="thread-permission",
        mode=mode,
        allow_sensitive=allow_sensitive,
        created_at=now,
        expires_at=now + timedelta(seconds=expires_in),
    )


class AuthorizationLeaseTests(unittest.TestCase):
    def test_autonomous_mode_allows_quant_workflow_but_not_memory_or_publication(self):
        current = lease()

        self.assertTrue(current.allows("prepare_qlib_dataset"))
        self.assertTrue(current.allows("run_governed_qlib_experiment"))
        self.assertFalse(current.allows("save_user_memory"))
        self.assertFalse(current.allows("delete_user_memory"))
        self.assertFalse(current.allows("publish_ai_trader_strategy"))

    def test_full_access_keeps_sensitive_tools_manual_until_explicitly_enabled(self):
        guarded = lease(mode="full_access")
        unrestricted = lease(mode="full_access", allow_sensitive=True)

        self.assertTrue(guarded.allows("save_user_memory"))
        self.assertFalse(guarded.allows("delete_user_memory"))
        self.assertFalse(guarded.allows("publish_ai_trader_discussion"))
        self.assertTrue(unrestricted.allows("delete_user_memory"))
        self.assertTrue(unrestricted.allows("publish_ai_trader_discussion"))

    def test_expired_lease_allows_no_effect(self):
        expired = lease(mode="full_access", allow_sensitive=True, expires_in=-1)

        self.assertFalse(expired.active)
        self.assertEqual(expired.allowed_tools, ())


if __name__ == "__main__":
    unittest.main()
