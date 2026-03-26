from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence, TypeVar

import requests

from chaos.config import ModelConfig
from chaos.memory import EMPTY_MEMORY_DIGEST, MemorySnapshot, MAX_PINNED_MEMORIES
from chaos.models import Role
from chaos.prompts import load_prompt
from chaos.runtime_log import RuntimeLogger


T = TypeVar("T")


PUBLIC_SPEECH_PREFIX = load_prompt("public_speech")
PRIVATE_REPLY_PREFIX = load_prompt("private_reply")
NUMBER_PREFIX = load_prompt("number_choice")
OPENING_SCENE_PREFIX = load_prompt("opening_scene")
ENVIRONMENT_PREFIX = load_prompt("environment")
MEMORY_DIGEST_PREFIX = load_prompt("memory_digest")


@dataclass(slots=True)
class LLMUsage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class HiddenThought:
    thought: str


@dataclass(slots=True)
class SpeechDecision:
    text: str
    thought: HiddenThought


@dataclass(slots=True)
class NumberDecision:
    value: int
    thought: HiddenThought


@dataclass(slots=True)
class ArkResponsesClient:
    config: ModelConfig
    logger: RuntimeLogger | None = None
    timeout: int = 60
    expire_after_seconds: int = 3600
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    debug: bool = False
    prefix_cache_ids: dict[str, str] = field(default_factory=dict)
    non_cacheable_prefixes: set[str] = field(default_factory=set)
    last_usage: LLMUsage = field(default_factory=LLMUsage)

    def generate_opening_scene(self, player: Role, contestant_count: int) -> str:
        payload = (
            f"玩家身份：{player.name}。\n"
            f"玩家背景：{player.background}\n"
            f"当前大厅内共有 {contestant_count} 人。\n"
            "玩家失去了大部分记忆，只知道必须活下去。"
        )
        return self._generate_text("opening_scene", OPENING_SCENE_PREFIX, payload, max_output_tokens=220)

    def generate_environment_reply(
        self,
        query: str,
        rules_announced: bool,
        visible_roles: Sequence[str],
    ) -> str:
        stage = "广播已宣读第 1 轮规则" if rules_announced else "广播尚未宣读规则"
        visible_text = "、".join(visible_roles)
        payload = (
            f"当前状态：{stage}\n"
            "公共环境：封闭大厅、白色灯光、消毒水气味、广播屏、倒计时装置、所有存活者始终同场。\n"
            f"当前可见存活者人数：{len(visible_roles)}\n"
            f"当前可见存活角色：{visible_text}\n"
            f"玩家问题：{query}\n"
            "回答应只基于上述公共事实，不能编造额外设施、出口、规则细节或他人内心。"
        )
        return self._generate_text("environment", ENVIRONMENT_PREFIX, payload, max_output_tokens=160)

    def generate_public_decision(
        self,
        role: Role,
        rules_announced: bool,
        recent_public_lines: Sequence[str] | None = None,
        memory_snapshot: MemorySnapshot | None = None,
    ) -> SpeechDecision:
        stage = "规则尚未宣读" if not rules_announced else "第 1 轮规则已宣读"
        recent_lines = list(recent_public_lines or [])
        recent_block = " / ".join(recent_lines) if recent_lines else "暂无"
        snapshot = memory_snapshot or MemorySnapshot()
        pinned_block = " / ".join(snapshot.pinned_memories) if snapshot.pinned_memories else "暂无"
        payload = (
            f"当前状态：{stage}\n"
            f"最近公共对话：{recent_block}\n"
            f"你的长期记忆锚点：{pinned_block}\n"
            f"你的滚动印象摘要：{snapshot.rolling_digest or EMPTY_MEMORY_DIGEST}\n"
            f"角色编号：{role.role_id}\n"
            f"角色姓名：{role.name}\n"
            f"年龄职业：{role.age_job}\n"
            f"背景：{role.background}\n"
            f"公开人格：{role.public_persona}\n"
            f"动机：{role.motive}\n"
            f"核心特征：{role.core_trait}\n"
            f"秘密：{role.secret}\n"
            f"底线：{role.taboo}\n"
            "现在请一次性返回内部思考和动作调用。"
        )
        return self._generate_json(
            "public_speech",
            PUBLIC_SPEECH_PREFIX,
            payload,
            max_output_tokens=160,
            validator=self._validate_public_decision,
        )

    def generate_private_decision(
        self,
        role: Role,
        player_text: str,
        recent_thread_lines: Sequence[str] | None = None,
        recent_public_lines: Sequence[str] | None = None,
        memory_snapshot: MemorySnapshot | None = None,
    ) -> SpeechDecision:
        thread_block = " / ".join(list(recent_thread_lines or [])) if recent_thread_lines else "暂无"
        public_block = " / ".join(list(recent_public_lines or [])) if recent_public_lines else "暂无"
        snapshot = memory_snapshot or MemorySnapshot()
        pinned_block = " / ".join(snapshot.pinned_memories) if snapshot.pinned_memories else "暂无"
        payload = (
            f"角色编号：{role.role_id}\n"
            f"角色姓名：{role.name}\n"
            f"年龄职业：{role.age_job}\n"
            f"背景：{role.background}\n"
            f"公开人格：{role.public_persona}\n"
            f"动机：{role.motive}\n"
            f"核心特征：{role.core_trait}\n"
            f"秘密：{role.secret}\n"
            f"底线：{role.taboo}\n"
            f"最近公开对话：{public_block}\n"
            f"你与001最近私聊：{thread_block}\n"
            f"你的长期记忆锚点：{pinned_block}\n"
            f"你的滚动印象摘要：{snapshot.rolling_digest or EMPTY_MEMORY_DIGEST}\n"
            f"玩家刚才私下对他说：{player_text}\n"
            "现在请一次性返回内部思考和动作调用。"
        )
        return self._generate_json(
            "private_reply",
            PRIVATE_REPLY_PREFIX,
            payload,
            max_output_tokens=160,
            validator=self._validate_private_decision,
        )

    def generate_number_decision(
        self,
        role: Role,
        recent_public_lines: Sequence[str] | None = None,
        memory_snapshot: MemorySnapshot | None = None,
    ) -> NumberDecision:
        public_block = " / ".join(list(recent_public_lines or [])) if recent_public_lines else "暂无"
        snapshot = memory_snapshot or MemorySnapshot()
        pinned_block = " / ".join(snapshot.pinned_memories) if snapshot.pinned_memories else "暂无"
        payload = (
            f"角色编号：{role.role_id}\n"
            f"角色姓名：{role.name}\n"
            f"年龄职业：{role.age_job}\n"
            f"背景：{role.background}\n"
            f"公开人格：{role.public_persona}\n"
            f"动机：{role.motive}\n"
            f"核心特征：{role.core_trait}\n"
            f"秘密：{role.secret}\n"
            f"底线：{role.taboo}\n"
            f"最近公开对话：{public_block}\n"
            f"你的长期记忆锚点：{pinned_block}\n"
            f"你的滚动印象摘要：{snapshot.rolling_digest or EMPTY_MEMORY_DIGEST}\n"
            "当前是第 1 轮《诱饵均值》，所有人已完成少量公开交流。现在请一次性返回内部思考和动作调用。"
        )
        return self._generate_json(
            "number_choice",
            NUMBER_PREFIX,
            payload,
            max_output_tokens=120,
            validator=self._validate_number_decision,
        )

    def generate_memory_digest(
        self,
        role: Role,
        previous_snapshot: MemorySnapshot,
        new_lines: Sequence[str],
        *,
        stage: str,
    ) -> MemorySnapshot:
        new_block = " / ".join(new_lines) if new_lines else "暂无"
        pinned_block = " / ".join(previous_snapshot.pinned_memories) if previous_snapshot.pinned_memories else "暂无"
        payload = (
            f"当前状态：{stage}\n"
            f"角色编号：{role.role_id}\n"
            f"角色姓名：{role.name}\n"
            f"年龄职业：{role.age_job}\n"
            f"背景：{role.background}\n"
            f"公开人格：{role.public_persona}\n"
            f"动机：{role.motive}\n"
            f"核心特征：{role.core_trait}\n"
            f"秘密：{role.secret}\n"
            f"底线：{role.taboo}\n"
            f"上一版长期记忆锚点：{pinned_block}\n"
            f"上一版滚动印象摘要：{previous_snapshot.rolling_digest}\n"
            f"这次新进入视野的信息：{new_block}\n"
            "请输出更新后的完整记忆状态。"
        )
        return self._generate_json(
            "memory_digest",
            MEMORY_DIGEST_PREFIX,
            payload,
            max_output_tokens=220,
            validator=self._validate_memory_digest,
        )

    def _generate_text(
        self,
        cache_name: str,
        prefix_prompt: str,
        user_input: str,
        *,
        max_output_tokens: int,
        json_object: bool = False,
    ) -> str:
        previous_response_id = self._ensure_prefix_cache(cache_name, prefix_prompt)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "store": True,
            "caching": {"type": "enabled"},
            "thinking": {"type": "disabled"},
            "max_output_tokens": max_output_tokens,
            "expire_at": int(time.time()) + self.expire_after_seconds,
        }
        if previous_response_id is not None:
            payload["previous_response_id"] = previous_response_id
            payload["input"] = [{"role": "user", "content": user_input}]
        else:
            payload["input"] = [
                {"role": "system", "content": prefix_prompt},
                {"role": "user", "content": user_input},
            ]
        if json_object:
            payload["text"] = {"format": {"type": "json_object"}}
        response = self._post_with_retry("/responses", payload)
        self._record_usage(response)
        text = self._extract_text(response)
        self._log_event(
            "llm_response",
            cache_name=cache_name,
            model=self.config.model,
            prefix_prompt=prefix_prompt,
            user_input=user_input,
            json_object=json_object,
            raw_text=text,
            usage={
                "input_tokens": self.last_usage.input_tokens,
                "cached_tokens": self.last_usage.cached_tokens,
                "output_tokens": self.last_usage.output_tokens,
            },
        )
        self._debug_log(f"[LLM RAW][{cache_name}] {text}")
        return text

    def _generate_json(
        self,
        cache_name: str,
        prefix_prompt: str,
        user_input: str,
        *,
        max_output_tokens: int,
        validator: Callable[[dict[str, Any]], T] | None = None,
    ) -> dict[str, Any] | T:
        last_error: Exception | None = None
        repair_hint = ""
        for attempt in range(self.max_retries + 1):
            retry_input = user_input
            if repair_hint:
                retry_input = (
                    f"{user_input}\n"
                    "上一次你的输出不合格。\n"
                    f"错误原因：{repair_hint}\n"
                    "这一次只允许返回单个合法 JSON 对象，不要附加解释、代码块、第二个对象或任何多余字符。"
                )
            try:
                text = self._generate_text(
                    cache_name,
                    prefix_prompt,
                    retry_input,
                    max_output_tokens=max_output_tokens,
                    json_object=True,
                )
                parsed = self._parse_json_object(text)
                if validator is None:
                    return parsed
                return validator(parsed)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                repair_hint = str(exc)
                if attempt >= self.max_retries:
                    raise
                self._log_event(
                    "llm_output_retry",
                    cache_name=cache_name,
                    attempt=attempt + 1,
                    error=repair_hint,
                )
                self._debug_log(
                    f"[LLM RETRY][{cache_name}] 第 {attempt + 1} 次输出不合法：{repair_hint}"
                )
                time.sleep(self.retry_backoff_seconds * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _ensure_prefix_cache(self, cache_name: str, prefix_prompt: str) -> str | None:
        existing = self.prefix_cache_ids.get(cache_name)
        if existing:
            return existing
        if cache_name in self.non_cacheable_prefixes:
            return None

        payload = {
            "model": self.config.model,
            "input": [{"role": "system", "content": prefix_prompt}],
            "store": True,
            "caching": {"type": "enabled", "prefix": True},
            "thinking": {"type": "disabled"},
            "expire_at": int(time.time()) + self.expire_after_seconds,
        }
        try:
            response = self._post_with_retry("/responses", payload)
        except requests.HTTPError as exc:
            if self._is_prefix_too_short_error(exc):
                self.non_cacheable_prefixes.add(cache_name)
                self._log_event(
                    "llm_prefix_cache_skipped",
                    cache_name=cache_name,
                    reason="prefix_too_short",
                )
                return None
            raise
        self._record_usage(response)
        response_id = response["id"]
        self.prefix_cache_ids[cache_name] = response_id
        self._log_event(
            "llm_prefix_cache_created",
            cache_name=cache_name,
            response_id=response_id,
            usage={
                "input_tokens": self.last_usage.input_tokens,
                "cached_tokens": self.last_usage.cached_tokens,
                "output_tokens": self.last_usage.output_tokens,
            },
        )
        return response_id

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + path
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text.strip()
            if detail:
                raise requests.HTTPError(f"{exc}. Response body: {detail}", response=response) from exc
            raise
        return response.json()

    def _post_with_retry(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._post(path, payload)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries or not self._is_retryable_request_error(exc):
                    raise
                self._log_event(
                    "llm_request_retry",
                    attempt=attempt + 1,
                    error=str(exc),
                )
                self._debug_log(
                    f"[LLM RETRY][request] 第 {attempt + 1} 次请求失败：{exc}"
                )
                time.sleep(self.retry_backoff_seconds * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _extract_text(self, response: dict[str, Any]) -> str:
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return content.get("text", "").strip()
        raise ValueError("Responses API 返回中未找到 output_text。")

    def _record_usage(self, response: dict[str, Any]) -> None:
        usage = response.get("usage", {})
        input_details = usage.get("input_tokens_details", {})
        self.last_usage = LLMUsage(
            input_tokens=int(usage.get("input_tokens", 0)),
            cached_tokens=int(input_details.get("cached_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )

    def _is_prefix_too_short_error(self, exc: requests.HTTPError) -> bool:
        response = exc.response
        if response is None:
            return False
        return (
            response.status_code == 400
            and "input tokens must be greater than 256 when using prefix cache" in response.text
        )

    def _is_retryable_request_error(self, exc: requests.RequestException) -> bool:
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError):
            response = exc.response
            if response is None:
                return True
            return response.status_code in {408, 409, 429} or response.status_code >= 500
        return False

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = self._strip_code_fence(candidate)
        if not candidate.startswith("{"):
            brace_index = candidate.find("{")
            if brace_index == -1:
                raise ValueError(f"LLM 输出中未找到 JSON 对象起始符。原始输出：{text!r}")
            candidate = candidate[brace_index:]
        decoder = json.JSONDecoder()
        parsed, _ = decoder.raw_decode(candidate)
        if not isinstance(parsed, dict):
            raise ValueError("LLM 动作输出必须是 JSON 对象。")
        return parsed

    def _strip_code_fence(self, text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
        return stripped

    def _validate_hidden_thought(self, parsed: dict[str, Any]) -> HiddenThought:
        thought = parsed.get("thought")
        if not isinstance(thought, str) or not thought.strip():
            raise ValueError("LLM 隐藏思考缺少非空 thought。")
        return HiddenThought(thought=thought.strip())

    def _validate_public_decision(self, parsed: dict[str, Any]) -> SpeechDecision:
        thought = self._validate_hidden_thought(parsed)
        text = self._extract_speak_text(parsed, target_type="public")
        return SpeechDecision(text=text, thought=thought)

    def _validate_private_decision(self, parsed: dict[str, Any]) -> SpeechDecision:
        thought = self._validate_hidden_thought(parsed)
        text = self._extract_speak_text(
            parsed,
            target_type="single",
            target_ids=["001"],
        )
        return SpeechDecision(text=text, thought=thought)

    def _validate_number_decision(self, parsed: dict[str, Any]) -> NumberDecision:
        thought = self._validate_hidden_thought(parsed)
        value = self._extract_number_value(parsed)
        return NumberDecision(value=max(0, min(100, value)), thought=thought)

    def _validate_memory_digest(self, parsed: dict[str, Any]) -> MemorySnapshot:
        rolling_digest = parsed.get("rolling_digest")
        if not isinstance(rolling_digest, str) or not rolling_digest.strip():
            raise ValueError("LLM 记忆更新缺少非空 rolling_digest。")
        pinned_memory = parsed.get("pinned_memory")
        if pinned_memory is None:
            pinned_items: list[str] = []
        elif isinstance(pinned_memory, list):
            pinned_items = []
            seen: set[str] = set()
            for item in pinned_memory:
                if not isinstance(item, str):
                    raise ValueError("LLM 记忆更新的 pinned_memory 必须是字符串数组。")
                normalized = item.strip()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                pinned_items.append(normalized)
        else:
            raise ValueError("LLM 记忆更新的 pinned_memory 必须是数组。")
        return MemorySnapshot(
            rolling_digest=rolling_digest.strip(),
            pinned_memories=pinned_items[:MAX_PINNED_MEMORIES],
        )

    def _debug_log(self, message: str) -> None:
        if self.debug:
            print(message)

    def _log_event(self, event: str, **payload: Any) -> None:
        if self.logger is not None:
            self.logger.log(event, **payload)

    def _extract_speak_text(
        self,
        action: dict[str, Any],
        *,
        target_type: str,
        target_ids: Sequence[str] | None = None,
    ) -> str:
        if action.get("action_name") != "speak":
            raise ValueError(f"LLM 返回了非法动作：{action.get('action_name')!r}，期望 'speak'。")
        args = action.get("args")
        if not isinstance(args, dict):
            raise ValueError("LLM speak 动作缺少 args 对象。")
        target_scope = args.get("target_scope")
        if not isinstance(target_scope, dict):
            raise ValueError("LLM speak 动作缺少 target_scope 对象。")
        if target_scope.get("type") != target_type:
            raise ValueError(
                f"LLM speak 动作 target_scope.type={target_scope.get('type')!r}，期望 {target_type!r}。"
            )
        if target_ids is not None:
            ids = target_scope.get("ids")
            if ids != list(target_ids):
                raise ValueError(f"LLM speak 动作 ids={ids!r}，期望 {list(target_ids)!r}。")
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("LLM speak 动作缺少非空 text。")
        return text.strip()

    def _extract_number_value(self, action: dict[str, Any]) -> int:
        if action.get("action_name") != "choose_number":
            raise ValueError(
                f"LLM 返回了非法动作：{action.get('action_name')!r}，期望 'choose_number'。"
            )
        args = action.get("args")
        if not isinstance(args, dict):
            raise ValueError("LLM choose_number 动作缺少 args 对象。")
        value = args.get("value")
        if not isinstance(value, int):
            raise ValueError(f"LLM choose_number.value 必须是整数，实际为 {value!r}。")
        return value
