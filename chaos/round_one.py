from __future__ import annotations

import random
from dataclasses import dataclass, field

from chaos.llm import ArkResponsesClient
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

    def opening_environment_scene(self) -> tuple[str, str]:
        llm = self._require_llm("opening_scene")
        try:
            return llm.generate_opening_scene(self.player, len(self.contestants)), "llm"
        except Exception as exc:
            raise self._llm_action_error("opening_scene", exc) from exc

    def broadcast_round_intro(self) -> str:
        self.rules_announced = True
        return (
            "广播 agent：第 1 轮《诱饵均值》即将开始。所有存活者先进行公开交流，"
            "随后各自秘密提交一个 0 到 100 的整数。目标数为全体真实平均值的一半，"
            "距离目标最近的前 16 名存活。"
        )

    def environment_reply(self, query: str) -> tuple[str, str]:
        normalized = query.strip().lower()
        visible_roles = [role.short_label for role in self.contestants]
        llm = self._require_llm("environment")
        try:
            text = llm.generate_environment_reply(
                query,
                self.rules_announced,
                visible_roles,
                roster_query=self._is_roster_query(normalized),
            )
        except Exception as exc:
            raise self._llm_action_error("environment", exc) from exc

        if normalized in {"look", "看看周围", "看看周围。", "这里是什么地方", "现在是什么情况"}:
            screen_state = "广播屏仍在待机" if not self.rules_announced else "广播屏已经切到第 1 轮规则界面"
            text = f"{text} 此刻大厅内共有 {len(visible_roles)} 人，{screen_state}。"
        if self._is_roster_query(normalized):
            roster = "、".join(visible_roles)
            text = f"{text} 此刻仍在你视线里的 {len(visible_roles)} 人是：{roster}。"
        return text, "llm"

    def seed_social_phase(self, count: int = 4) -> list[Message]:
        speakers = self.rng.sample([role for role in self.contestants if not role.is_player], k=count)
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
            self.messages.append(message)
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
        self.messages.append(message)
        return message

    def npc_public_replies(self, count: int = 2) -> list[Message]:
        eligible = [role for role in self.contestants if not role.is_player]
        speaker_count = min(count, len(eligible))
        speakers = self.rng.sample(eligible, k=speaker_count)
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
            self.messages.append(message)
            replies.append(message)
        return replies

    def environment_message(self, query: str) -> Message:
        text, source = self.environment_reply(query)
        return Message(
            speaker_id="environment",
            speaker_name="环境 agent",
            text=text,
            visibility="private",
            source=source,
            recipients=[self.player.role_id],
        )

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
        self.private_threads.setdefault(target.role_id, []).append(message)
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
        self.private_threads.setdefault(target.role_id, []).append(message)
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

    def _npc_public_line(self, role: Role, index: int) -> tuple[str, str]:
        del index
        llm = self._require_llm("public_speech")
        try:
            decision = llm.generate_public_decision(
                role,
                self.rules_announced,
                self._recent_public_lines(),
                self._memory_digest_for(role),
            )
        except Exception as exc:
            raise self._llm_action_error("public_speech", exc) from exc

        self._record_trace(
            role=role,
            stage="public_speech",
            thought=decision.thought.thought,
            intent=decision.thought.intent,
            attitude=decision.thought.attitude,
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
        try:
            decision = llm.generate_private_decision(
                role,
                player_text,
                self._recent_private_lines(role.role_id),
                self._recent_public_lines(),
                self._memory_digest_for(role),
            )
        except Exception as exc:
            raise self._llm_action_error("private_reply", exc) from exc

        self._record_trace(
            role=role,
            stage="private_reply",
            thought=decision.thought.thought,
            intent=decision.thought.intent,
            attitude=decision.thought.attitude,
            action_name="speak",
            action_summary=decision.text,
        )
        return decision.text, "llm"

    def _npc_number(self, role: Role) -> int:
        llm = self._require_llm("number_choice")
        try:
            decision = llm.generate_number_decision(
                role,
                self._recent_public_lines(limit=6),
                self._memory_digest_for(role),
            )
        except Exception as exc:
            raise self._llm_action_error("number_choice", exc) from exc

        self._record_trace(
            role=role,
            stage="choose_number",
            thought=decision.thought.thought,
            intent=decision.thought.intent,
            attitude=decision.thought.attitude,
            action_name="choose_number",
            action_summary=str(decision.value),
        )
        return decision.value

    def _recent_public_lines(self, limit: int = 6) -> list[str]:
        public_messages = [message for message in self.messages if message.visibility == "public"]
        return [self._message_context_line(message) for message in public_messages[-limit:]]

    def _recent_private_lines(self, role_id: str, limit: int = 4) -> list[str]:
        thread = self.private_threads.get(role_id, [])
        return [self._message_context_line(message) for message in thread[-limit:]]

    def _memory_digest_for(self, role: Role) -> str:
        notes: list[str] = []
        if any(message.visibility == "public" for message in self.messages):
            notes.append("你记得场上刚刚已经出现过几轮公开试探，气氛正在变得更具体。")
        private_summary = self._private_thread_summary(role.role_id)
        if private_summary:
            notes.append(private_summary)
        player_probe = self._private_inference(role.role_id)
        if player_probe:
            notes.append(player_probe)
        if self.rules_announced:
            notes.append("广播已经宣读第1轮规则，所有人都开始围绕报数和误导彼此试探。")
        else:
            notes.append("广播尚未宣读规则，所有人还处在观察环境和彼此摸底的阶段。")
        if not notes:
            return "你刚醒来不久，还没有形成稳定判断。"
        return " ".join(notes)

    def _private_inference(self, role_id: str) -> str:
        thread = self.private_threads.get(role_id, [])
        if not thread:
            return ""
        player_messages = [message.text for message in thread if message.speaker_id == self.player.role_id]
        if not player_messages:
            return ""
        latest = player_messages[-1]
        if any(token in latest for token in ("你是谁", "你什么人", "你叫什么")):
            return "001 一上来就在摸你的身份和底色，这更像试探，不必直接交底。"
        if any(token in latest for token in ("报", "数字", "多少")):
            return "001 正在试探你的报数倾向，未必是真的来交换信息。"
        if any(token in latest for token in ("合作", "一起", "联手", "帮我")):
            return "001 可能在试探拉拢或钓承诺，你要判断这是不是临时利用。"
        return "001 刚才的私聊值得记住，他在通过提问确认你是否可用、是否可信。"

    def _private_thread_summary(self, role_id: str) -> str:
        thread = self.private_threads.get(role_id, [])
        if not thread:
            return ""
        exchange_count = sum(1 for message in thread if message.speaker_id in {self.player.role_id, role_id})
        if exchange_count <= 1:
            return "你刚和001有过一次私下接触，对方正在观察你是否会接话。"
        return f"你和001刚经历了 {exchange_count} 句私聊往返，这段接触会影响你接下来的判断。"

    def _message_context_line(self, message: Message) -> str:
        return f"{self._context_speaker_label(message)}: {message.text}"

    def _context_speaker_label(self, message: Message) -> str:
        if message.speaker_id == self.player.role_id:
            return f"{self.player.role_id}（玩家）"
        if message.speaker_id == "environment":
            return "环境 agent"
        return f"{message.speaker_id} {message.speaker_name}"

    def _record_trace(
        self,
        *,
        role: Role,
        stage: str,
        thought: str,
        intent: str,
        attitude: str,
        action_name: str,
        action_summary: str,
    ) -> None:
        trace = DecisionTrace(
            role_id=role.role_id,
            role_name=role.name,
            stage=stage,
            thought=thought,
            intent=intent,
            attitude=attitude,
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
                intent=trace.intent,
                attitude=trace.attitude,
                action_name=trace.action_name,
                action_summary=trace.action_summary,
            )

    def _is_roster_query(self, normalized: str) -> bool:
        return normalized in {"who", "现在谁在附近", "谁在附近", "现在还有谁", "这里都有谁"}

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
