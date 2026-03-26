from __future__ import annotations

import random
from dataclasses import dataclass, field

from chaos.llm import ArkResponsesClient
from chaos.memory import MemoryContextBuilder, PUBLIC_MEMORY_WINDOW, RoleMemoryStore
from chaos.models import DecisionTrace, Message, Role, RoundOneResult


class LLMActionError(RuntimeError):
    def __init__(self, capability: str, message: str) -> None:
        super().__init__(message)
        self.capability = capability


@dataclass(slots=True)
class RoundOneGame:
    player: Role
    contestants: list[Role]
    llm: ArkResponsesClient | None = None
    rng: random.Random = field(default_factory=lambda: random.Random(7))
    rules_announced: bool = False
    messages: list[Message] = field(default_factory=list)
    private_threads: dict[str, list[Message]] = field(default_factory=dict)
    submissions: dict[str, int] = field(default_factory=dict)
    decision_traces: list[DecisionTrace] = field(default_factory=list)
    memory_store: RoleMemoryStore = field(init=False)
    public_turn_index: int = 0
    public_last_spoke_turn: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.memory_store = RoleMemoryStore(
            player=self.player,
            contestants=self.contestants,
        )

    def opening_environment_message(self) -> Message:
        llm = self._require_llm("opening_scene")
        try:
            text = llm.generate_opening_scene(self.player, len(self.contestants))
        except Exception as exc:
            raise self._llm_action_error("opening_scene", exc) from exc
        message = Message(
            speaker_id="environment",
            speaker_name="环境 agent",
            text=text,
            visibility="public",
            source="llm",
            recipients=[contestant.role_id for contestant in self.contestants],
        )
        self._append_public_message(message)
        return message

    def broadcast_round_intro_message(self) -> Message:
        self.rules_announced = True
        message = Message(
            speaker_id="broadcast",
            speaker_name="广播 agent",
            text=(
                "第 1 轮《诱饵均值》即将开始。所有存活者先进行公开交流，"
                "随后各自秘密提交一个 0 到 100 的整数。目标数为全体真实平均值的一半，"
                "距离目标最近的前 16 名存活。"
            ),
            visibility="public",
            source="system",
            recipients=[contestant.role_id for contestant in self.contestants],
        )
        self._append_public_message(message)
        return message

    def environment_reply(self, query: str) -> tuple[str, str]:
        visible_roles = [role.short_label for role in self.contestants]
        llm = self._require_llm("environment")
        try:
            text = llm.generate_environment_reply(
                query,
                self.rules_announced,
                visible_roles,
            )
        except Exception as exc:
            raise self._llm_action_error("environment", exc) from exc
        return text, "llm"

    def seed_social_phase(self) -> list[Message]:
        speakers = self._pick_public_speakers(
            target_count=self._opening_public_budget(),
            trigger_speaker_id=None,
            trigger_text="",
        )
        seeded: list[Message] = []
        for index, role in enumerate(speakers):
            text, source = self._npc_public_line(role, index)
            message = Message(
                speaker_id=role.role_id,
                speaker_name=role.name,
                text=text,
                visibility="public",
                source=source,
                recipients=[contestant.role_id for contestant in self.contestants],
            )
            self._append_public_message(message)
            seeded.append(message)
        return seeded

    def player_public_speak(self, text: str) -> Message:
        message = Message(
            speaker_id=self.player.role_id,
            speaker_name=self.player.name,
            text=text.strip(),
            visibility="public",
            source="player",
            recipients=[contestant.role_id for contestant in self.contestants],
        )
        self._append_public_message(message)
        return message

    def player_environment_speak(self, text: str) -> Message:
        message = Message(
            speaker_id=self.player.role_id,
            speaker_name=self.player.name,
            text=text.strip(),
            visibility="private",
            source="player",
            recipients=["environment"],
        )
        self._append_message_log_only(message)
        return message

    def npc_public_replies(self, trigger_text: str, trigger_speaker_id: str) -> list[Message]:
        speakers = self._pick_public_speakers(
            target_count=self._public_reply_budget(trigger_text),
            trigger_speaker_id=trigger_speaker_id,
            trigger_text=trigger_text,
        )
        replies: list[Message] = []
        for index, role in enumerate(speakers):
            text, source = self._npc_public_line(role, index)
            message = Message(
                speaker_id=role.role_id,
                speaker_name=role.name,
                text=text,
                visibility="public",
                source=source,
                recipients=[contestant.role_id for contestant in self.contestants],
            )
            self._append_public_message(message)
            replies.append(message)
        return replies

    def environment_message(self, query: str) -> Message:
        text, source = self.environment_reply(query)
        message = Message(
            speaker_id="environment",
            speaker_name="环境 agent",
            text=text,
            visibility="private",
            source=source,
            recipients=[self.player.role_id],
        )
        self._append_message_log_only(message)
        return message

    def player_private_speak(self, target_id: str, text: str) -> Message:
        target = self.find_role(target_id)
        message = Message(
            speaker_id=self.player.role_id,
            speaker_name=self.player.name,
            text=text.strip(),
            visibility="private",
            source="player",
            recipients=[self.player.role_id, target.role_id],
        )
        self._append_private_message(target.role_id, message)
        return message

    def npc_private_reply(self, target_id: str) -> Message:
        target = self.find_role(target_id)
        text, source = self._npc_private_line(target)
        message = Message(
            speaker_id=target.role_id,
            speaker_name=target.name,
            text=text,
            visibility="private",
            source=source,
            recipients=[self.player.role_id, target.role_id],
        )
        self._append_private_message(target.role_id, message)
        return message

    def submit_player_number(self, value: int) -> None:
        self.submissions[self.player.role_id] = value

    def auto_submit_npc_numbers(self) -> None:
        pending: dict[str, int] = {}
        for role in self.contestants:
            if role.is_player:
                continue
            pending[role.role_id] = self._npc_number(role)
        self.submissions.update(pending)

    def resolve(self) -> RoundOneResult:
        rankings: list[tuple[Role, int, float]] = []
        avg = sum(self.submissions.values()) / len(self.submissions)
        target = avg / 2
        for role in self.contestants:
            value = self.submissions[role.role_id]
            distance = abs(value - target)
            rankings.append((role, value, distance))
        rankings.sort(key=lambda item: (item[2], item[1], item[0].role_id))
        survivors = [item[0] for item in rankings[:16]]
        eliminated = [item[0] for item in rankings[16:]]
        return RoundOneResult(
            average=avg,
            target=target,
            rankings=rankings,
            survivors=survivors,
            eliminated=eliminated,
        )

    def find_role(self, role_id: str) -> Role:
        for role in self.contestants:
            if role.role_id == role_id:
                return role
        raise ValueError(f"未知角色编号: {role_id}")

    def list_alive(self) -> list[Role]:
        return self.contestants

    def _memory(self) -> MemoryContextBuilder:
        return self.memory_store.build_context(
            messages=self.messages,
            private_threads=self.private_threads,
            rules_announced=self.rules_announced,
        )

    def _refresh_role_memory(self, role: Role) -> None:
        self.memory_store.refresh_digest_for(
            role,
            self.llm,
            rules_announced=self.rules_announced,
        )

    def _append_public_message(self, message: Message) -> None:
        self.messages.append(message)
        self.public_turn_index += 1
        self.public_last_spoke_turn[message.speaker_id] = self.public_turn_index
        self.memory_store.observe_message(message)

    def _append_message_log_only(self, message: Message) -> None:
        self.messages.append(message)

    def _append_private_message(self, role_id: str, message: Message) -> None:
        self.private_threads.setdefault(role_id, []).append(message)
        self.memory_store.observe_message(message)

    def _opening_public_budget(self) -> int:
        options = [1, 1, 2, 2, 3] if not self.rules_announced else [1, 2, 2, 3, 3]
        return options[self.rng.randrange(len(options))]

    def _public_reply_budget(self, trigger_text: str) -> int:
        text = trigger_text.strip()
        intensity = 0
        if any(token in text for token in ("？", "?", "谁", "怎么", "为什么", "凭什么")):
            intensity += 1
        if any(token in text for token in ("报", "数字", "均值", "合作", "联手", "站队", "骗", "带节奏")):
            intensity += 1
        if len(text) >= 18:
            intensity += 1

        if self.rules_announced:
            options = [0, 1, 1, 2] if intensity <= 0 else [1, 1, 2, 2, 3]
        else:
            options = [0, 0, 1, 1, 2] if intensity <= 0 else [0, 1, 1, 2, 2]
        return options[self.rng.randrange(len(options))]

    def _pick_public_speakers(
        self,
        *,
        target_count: int,
        trigger_speaker_id: str | None,
        trigger_text: str,
    ) -> list[Role]:
        eligible = [
            role
            for role in self.contestants
            if not role.is_player and role.role_id != trigger_speaker_id
        ]
        if not eligible or target_count <= 0:
            return []

        lowered_text = trigger_text.lower()
        scored: list[tuple[float, Role]] = []
        for role in eligible:
            score = 1.0 + self.rng.random() * 0.8
            last_turn = self.public_last_spoke_turn.get(role.role_id)
            if last_turn is not None:
                gap = self.public_turn_index - last_turn
                if gap <= 1:
                    score *= 0.05
                elif gap <= 3:
                    score *= 0.35
                elif gap <= 5:
                    score *= 0.7
            else:
                score *= 1.2

            if lowered_text:
                if role.role_id.lower() in lowered_text or role.name.lower() in lowered_text:
                    score += 3.0
                if any(token in trigger_text for token in ("？", "?")):
                    score += 0.4
                if self.rules_announced and any(
                    token in trigger_text for token in ("报", "数字", "均值", "合作", "联手", "骗", "带节奏")
                ):
                    score += 0.8
            scored.append((score, role))

        scored.sort(key=lambda item: item[0], reverse=True)
        pool = scored[: min(len(scored), max(target_count + 2, target_count))]
        chosen: list[Role] = []
        speaker_count = min(target_count, len(pool))
        while pool and len(chosen) < speaker_count:
            total = sum(score for score, _ in pool)
            pick = self.rng.random() * total
            cursor = 0.0
            for index, (score, role) in enumerate(pool):
                cursor += score
                if pick <= cursor:
                    chosen.append(role)
                    pool.pop(index)
                    break
        return chosen

    def _npc_public_line(self, role: Role, index: int) -> tuple[str, str]:
        del index
        llm = self._require_llm("public_speech")
        self._refresh_role_memory(role)
        memory = self._memory()
        try:
            decision = llm.generate_public_decision(
                role,
                self.rules_announced,
                memory.recent_public_lines(),
                memory.memory_snapshot_for(role),
            )
        except Exception as exc:
            raise self._llm_action_error("public_speech", exc) from exc

        self._record_trace(
            role=role,
            stage="public_speech",
            thought=decision.thought.thought,
            action_name="speak",
            action_summary=decision.text,
        )
        return decision.text, "llm"

    def _npc_private_line(self, role: Role) -> tuple[str, str]:
        if self.private_threads.get(role.role_id):
            player_text = self.private_threads[role.role_id][-1].text
        else:
            player_text = ""
        llm = self._require_llm("private_reply")
        self._refresh_role_memory(role)
        memory = self._memory()
        try:
            decision = llm.generate_private_decision(
                role,
                player_text,
                memory.recent_private_lines(role.role_id),
                memory.recent_public_lines(),
                memory.memory_snapshot_for(role),
            )
        except Exception as exc:
            raise self._llm_action_error("private_reply", exc) from exc

        self._record_trace(
            role=role,
            stage="private_reply",
            thought=decision.thought.thought,
            action_name="speak",
            action_summary=decision.text,
        )
        return decision.text, "llm"

    def _npc_number(self, role: Role) -> int:
        llm = self._require_llm("number_choice")
        self._refresh_role_memory(role)
        memory = self._memory()
        try:
            decision = llm.generate_number_decision(
                role,
                memory.recent_public_lines(limit=PUBLIC_MEMORY_WINDOW),
                memory.memory_snapshot_for(role),
            )
        except Exception as exc:
            raise self._llm_action_error("number_choice", exc) from exc

        self._record_trace(
            role=role,
            stage="choose_number",
            thought=decision.thought.thought,
            action_name="choose_number",
            action_summary=str(decision.value),
        )
        return decision.value

    def _record_trace(
        self,
        *,
        role: Role,
        stage: str,
        thought: str,
        action_name: str,
        action_summary: str,
    ) -> None:
        trace = DecisionTrace(
            role_id=role.role_id,
            role_name=role.name,
            stage=stage,
            thought=thought,
            action_name=action_name,
            action_summary=action_summary,
        )
        self.decision_traces.append(trace)
        if self.llm is not None:
            self.llm._log_event(
                "npc_decision",
                role_id=trace.role_id,
                role_name=trace.role_name,
                stage=trace.stage,
                thought=trace.thought,
                action_name=trace.action_name,
                action_summary=trace.action_summary,
            )

    def _require_llm(self, capability: str) -> ArkResponsesClient:
        if self.llm is None:
            raise LLMActionError(
                capability,
                f"[LLM] {capability} 当前不可用，未配置可继续调用的模型。你可以继续输入其他命令，稍后再试。",
            )
        return self.llm

    def _llm_action_error(self, capability: str, exc: Exception) -> LLMActionError:
        return LLMActionError(
            capability,
            f"[LLM] {capability} 调用失败，重试后仍未成功：{exc}。这次动作没有执行，你可以继续输入命令重试。",
        )
