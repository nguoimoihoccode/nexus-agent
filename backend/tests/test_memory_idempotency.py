import unittest
from unittest.mock import patch

from source.agents.governed_effects import GovernedEffectDenied
from source.agents.memory import save_user_memory
from source.agents.factory import MEMORY_APPROVAL_POLICY, SUPERVISOR_SYSTEM_PROMPT
from source.domain import (
    approval_action_identity,
    normalize_governed_arguments,
    normalize_memory_key,
)
from source.infrastructure.memory_repository import (
    normalize_user_memory,
    render_user_memory,
)


class UserMemoryPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_tool_rejects_direct_calls_without_governed_capability(self) -> None:
        with (
            patch("source.agents.memory.require_domain_pool") as require_pool,
            self.assertRaisesRegex(GovernedEffectDenied, "governed_approval_required"),
        ):
            await save_user_memory.ainvoke(
                {
                    "memory_key": "preference.response_style",
                    "content": "concise",
                }
            )
        require_pool.assert_not_called()

    def test_memory_normalization_is_bounded_and_stable(self) -> None:
        content, digest = normalize_user_memory("  durable preference  ")

        self.assertEqual(content, "durable preference")
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        with self.assertRaisesRegex(ValueError, "empty"):
            normalize_user_memory(" \n ")
        with self.assertRaisesRegex(ValueError, "16 KiB"):
            normalize_user_memory("x" * (16 * 1024 + 1))

    def test_approval_identity_never_persists_raw_memory(self) -> None:
        raw = "private durable preference"
        normalized = normalize_governed_arguments(
            "save_user_memory",
            {"memory_key": "preference.response_style", "content": raw},
        )
        target, digest = approval_action_identity(
            actor_key="v1-" + "a" * 64,
            tool_name="save_user_memory",
            normalized_arguments=normalized,
        )

        self.assertEqual(target, "postgres:user-memory")
        self.assertRegex(digest, r"^act_v1_[0-9a-f]{64}$")
        self.assertNotIn(raw, str(normalized))
        self.assertEqual(normalized["memory_key"], "preference.response_style")
        self.assertEqual(normalized["content_bytes"], len(raw.encode("utf-8")))

    def test_memory_keys_and_aggregate_are_stable(self) -> None:
        self.assertEqual(normalize_memory_key(" Identity.Name "), "identity.name")
        with self.assertRaisesRegex(ValueError, "lowercase letters"):
            normalize_memory_key("identity/name")

        rendered = render_user_memory(
            {
                "preference.game.example": "Enjoys a multiplayer game.",
                "identity.name": "Preferred name is Example User.",
            }
        )
        self.assertEqual(
            rendered,
            "[identity.name]\nPreferred name is Example User.\n\n"
            "[preference.game.example]\nEnjoys a multiplayer game.",
        )

    def test_supervisor_uses_explicit_memory_tools_and_code_prompt(self) -> None:
        self.assertEqual(
            set(MEMORY_APPROVAL_POLICY),
            {"save_user_memory", "delete_user_memory"},
        )
        self.assertIn("explicitly asks", SUPERVISOR_SYSTEM_PROMPT)
        self.assertIn("stable generic memory key", SUPERVISOR_SYSTEM_PROMPT)
        self.assertIn("never\noverwrite unrelated facts", SUPERVISOR_SYSTEM_PROMPT)
        self.assertIn("never repeat", SUPERVISOR_SYSTEM_PROMPT)

    def test_each_new_delete_call_has_a_distinct_approval_identity(self) -> None:
        first = normalize_governed_arguments(
            "delete_user_memory", {}, tool_call_id="memory-delete-1"
        )
        second = normalize_governed_arguments(
            "delete_user_memory", {}, tool_call_id="memory-delete-2"
        )
        first_identity = approval_action_identity(
            actor_key="v1-" + "a" * 64,
            tool_name="delete_user_memory",
            normalized_arguments=first,
        )
        second_identity = approval_action_identity(
            actor_key="v1-" + "a" * 64,
            tool_name="delete_user_memory",
            normalized_arguments=second,
        )

        self.assertNotEqual(first_identity, second_identity)


if __name__ == "__main__":
    unittest.main()
