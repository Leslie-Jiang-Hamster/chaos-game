from __future__ import annotations

import unittest
from unittest import mock

import chaos.web as web_module
from chaos.llm import HiddenThought, NumberDecision, SpeechDecision
from chaos.memory import MemorySnapshot
from chaos.models import Role
from chaos.web import WebGameSession


class FakeLLM:
    def generate_opening_scene(self, player: Role, contestant_count: int) -> str:
        return f"测试开场：{player.name}看到{contestant_count}人。"

    def generate_environment_reply(self, query: str, rules_announced: bool, visible_roles: list[str]) -> str:
        del rules_announced, visible_roles
        return f"环境回应：{query}"

    def generate_public_decision(
        self,
        role: Role,
        rules_announced: bool,
        recent_public_lines: list[str] | None = None,
        memory_snapshot: MemorySnapshot | None = None,
    ) -> SpeechDecision:
        del rules_announced, recent_public_lines, memory_snapshot
        return SpeechDecision(
            text=f"{role.name}（测试）公开发言。",
            thought=HiddenThought("test-public"),
        )

    def generate_private_decision(
        self,
        role: Role,
        player_text: str,
        recent_thread_lines: list[str] | None = None,
        recent_public_lines: list[str] | None = None,
        memory_snapshot: MemorySnapshot | None = None,
    ) -> SpeechDecision:
        del recent_thread_lines, recent_public_lines, memory_snapshot
        return SpeechDecision(
            text=f"{role.name}（测试）私聊回复：{player_text or '收到'}",
            thought=HiddenThought("test-private"),
        )

    def generate_number_decision(
        self,
        role: Role,
        recent_public_lines: list[str] | None = None,
        memory_snapshot: MemorySnapshot | None = None,
    ) -> NumberDecision:
        del recent_public_lines, memory_snapshot
        return NumberDecision(
            value=(int(role.role_id) * 7) % 101,
            thought=HiddenThought("test-number"),
        )

    def generate_memory_digest(
        self,
        role: Role,
        previous_snapshot: MemorySnapshot,
        new_lines: list[str],
        *,
        stage: str,
    ) -> MemorySnapshot:
        del role, new_lines, stage
        return MemorySnapshot(
            rolling_digest=previous_snapshot.rolling_digest or "test-digest",
            pinned_memories=list(previous_snapshot.pinned_memories),
        )

    def _log_event(self, event: str, **payload: object) -> None:
        del event, payload


class WebSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._llm_patcher = mock.patch.object(
            web_module,
            "_build_llm_client",
            side_effect=FakeLLM,
        )
        self._llm_patcher.start()
        self.addCleanup(self._llm_patcher.stop)

    def _make_session(self) -> WebGameSession:
        session = WebGameSession()
        self.addCleanup(session.close)
        return session

    def test_bootstrap_payload_basics(self) -> None:
        session = self._make_session()
        payload = session.bootstrap_payload()
        self.assertIn("messages", payload)
        self.assertIn("conversations", payload)
        self.assertEqual(payload["state"]["phase_id"], "free_social")
        self.assertEqual(payload["state"]["alive_count"], 10)
        self.assertEqual(payload["player"]["role_id"], "001")

    def test_choose_requires_execution_phase(self) -> None:
        session = self._make_session()
        with self.assertRaisesRegex(ValueError, "主持人还没宣读规则"):
            session.choose_number(10)

    def test_unknown_conversation_rejected(self) -> None:
        session = self._make_session()
        with self.assertRaisesRegex(ValueError, "未知会话"):
            session.send_message("foobar", "hello")

    def test_unknown_role_in_private_conversation_rejected(self) -> None:
        session = self._make_session()
        with self.assertRaisesRegex(ValueError, "未知角色编号"):
            session.send_message("private:999", "hello")

    def test_environment_private_conversation_rejected(self) -> None:
        session = self._make_session()
        with self.assertRaisesRegex(ValueError, "环境 agent 不支持私聊"):
            session.send_message("private:environment", "hello")

    def test_private_host_conversation_supported(self) -> None:
        session = self._make_session()
        delta = session.send_message("private:broadcast", "请复述规则")
        self.assertTrue(any(message["conversation_id"] == "private:broadcast" for message in delta["messages"]))
        self.assertTrue(any(message["speaker_id"] == "broadcast" for message in delta["messages"]))

    def test_environment_public_messages_route_to_lobby(self) -> None:
        session = self._make_session()
        payload = session.bootstrap_payload()
        environment_messages = [
            message
            for message in payload["messages"]
            if message["speaker_id"] == "environment"
        ]
        self.assertTrue(environment_messages)
        self.assertTrue(all(message["conversation_id"] == "lobby" for message in environment_messages))

    def test_conversation_list_contains_private_host(self) -> None:
        session = self._make_session()
        payload = session.bootstrap_payload()
        conversation_ids = {conversation["id"] for conversation in payload["conversations"]}
        self.assertIn("private:broadcast", conversation_ids)
        self.assertNotIn("broadcast", conversation_ids)
        self.assertNotIn("private:environment", conversation_ids)
        self.assertEqual(len(payload["conversations"]), 11)

    def test_round_can_resolve_after_transition_and_choose(self) -> None:
        session = self._make_session()
        session.end_phase()
        choose_payload = session.choose_number(42)
        self.assertTrue(choose_payload["ok"])
        self.assertTrue(choose_payload["state"]["player_has_chosen"])
        delta = session.end_phase()
        self.assertEqual(delta["state"]["phase_id"], "resolved")
        self.assertIsNotNone(delta["state"]["result"])
        self.assertEqual(len(delta["state"]["result"]["survivors"]), 8)
        self.assertEqual(len(delta["state"]["result"]["eliminated"]), 2)


if __name__ == "__main__":
    unittest.main()
