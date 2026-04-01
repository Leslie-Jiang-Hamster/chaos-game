from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Sequence

from chaos.models import Message, Role

if TYPE_CHECKING:
    from chaos.llm import ArkResponsesClient


PUBLIC_MEMORY_WINDOW = 12
PRIVATE_MEMORY_WINDOW = 8
EMPTY_MEMORY_DIGEST = "你刚醒来不久，还没有形成稳定判断。"
MAX_PINNED_MEMORIES = 6


@dataclass(slots=True)
class MemorySnapshot:
    rolling_digest: str = EMPTY_MEMORY_DIGEST
    pinned_memories: list[str] = field(default_factory=list)


def format_message_context_line(player: Role, message: Message) -> str:
    return f"{context_speaker_label(player, message)}: {message.text}"


def context_speaker_label(player: Role, message: Message) -> str:
    if message.speaker_id == player.role_id:
        return f"{player.role_id}（玩家）"
    if message.speaker_id == "environment":
        return "环境 agent"
    if message.speaker_id == "broadcast":
        return "主持人"
    return f"{message.speaker_id} {message.speaker_name}"


@dataclass(slots=True)
class RoleMemoryStore:
    player: Role
    contestants: Sequence[Role]
    rolling_digests: dict[str, str] = field(default_factory=dict)
    pinned_memories: dict[str, list[str]] = field(default_factory=dict)
    pending_events: dict[str, list[str]] = field(default_factory=dict)

    def build_context(
        self,
        messages: Sequence[Message],
        private_threads: Mapping[str, Sequence[Message]],
        rules_announced: bool,
    ) -> "MemoryContextBuilder":
        return MemoryContextBuilder(
            player=self.player,
            messages=messages,
            private_threads=private_threads,
            rules_announced=rules_announced,
            rolling_digests=self.rolling_digests,
            pinned_memories=self.pinned_memories,
        )

    def observe_message(
        self,
        message: Message,
    ) -> None:
        visible_roles = self._visible_roles_for(message)
        if not visible_roles:
            return
        event_line = format_message_context_line(self.player, message)
        for role in visible_roles:
            self.pending_events.setdefault(role.role_id, []).append(event_line)

    def refresh_digest_for(
        self,
        role: Role,
        llm: "ArkResponsesClient | None",
        *,
        rules_announced: bool,
    ) -> None:
        if llm is None:
            return
        pending_lines = self.pending_events.get(role.role_id, [])
        if not pending_lines:
            return
        previous_snapshot = self.snapshot_for(role)
        stage = "主持人已宣读第 1 轮规则" if rules_announced else "主持人尚未宣读规则"
        updated_snapshot = llm.generate_memory_digest(
            role,
            previous_snapshot,
            pending_lines,
            stage=stage,
        )
        self.rolling_digests[role.role_id] = (
            updated_snapshot.rolling_digest.strip() or previous_snapshot.rolling_digest
        )
        self.pinned_memories[role.role_id] = updated_snapshot.pinned_memories[:MAX_PINNED_MEMORIES]
        self.pending_events[role.role_id] = []

    def snapshot_for(self, role: Role) -> MemorySnapshot:
        return MemorySnapshot(
            rolling_digest=self.rolling_digests.get(role.role_id, EMPTY_MEMORY_DIGEST),
            pinned_memories=list(self.pinned_memories.get(role.role_id, [])),
        )

    def _visible_roles_for(self, message: Message) -> list[Role]:
        visible_ids: set[str] = set()
        if message.visibility == "public":
            visible_ids = {role.role_id for role in self.contestants if not role.is_player}
        else:
            visible_ids = {
                role.role_id
                for role in self.contestants
                if not role.is_player and role.role_id in set(message.recipients)
            }
            if message.speaker_id in {role.role_id for role in self.contestants if not role.is_player}:
                visible_ids.add(message.speaker_id)
        return [role for role in self.contestants if role.role_id in visible_ids]


@dataclass(slots=True)
class MemoryContextBuilder:
    player: Role
    messages: Sequence[Message]
    private_threads: Mapping[str, Sequence[Message]]
    rules_announced: bool
    rolling_digests: Mapping[str, str]
    pinned_memories: Mapping[str, Sequence[str]]

    def recent_public_lines(self, limit: int = PUBLIC_MEMORY_WINDOW) -> list[str]:
        public_messages = [message for message in self.messages if message.visibility == "public"]
        return [format_message_context_line(self.player, message) for message in public_messages[-limit:]]

    def recent_private_lines(self, role_id: str, limit: int = PRIVATE_MEMORY_WINDOW) -> list[str]:
        thread = self.private_threads.get(role_id, [])
        return [format_message_context_line(self.player, message) for message in thread[-limit:]]

    def memory_snapshot_for(self, role: Role) -> MemorySnapshot:
        return MemorySnapshot(
            rolling_digest=self.rolling_digests.get(role.role_id, EMPTY_MEMORY_DIGEST),
            pinned_memories=list(self.pinned_memories.get(role.role_id, [])),
        )
