from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence, TypeVar

import requests

from chaos.config import ModelConfig
from chaos.models import Role
from chaos.runtime_log import RuntimeLogger


T = TypeVar("T")


PUBLIC_SPEECH_PREFIX = """
收到角色资料后，你就是那个角色本人。
你要在一次输出里同时给出玩家不可见的内部思考，以及这次真正要执行的对外动作调用。
基础规则：
1. 除了思考，你的对外输出必须是动作调用，不能直接输出散文、对白、解释或旁白。
2. 当前这次请求里，你只能返回一个 JSON 对象，表示一次动作调用。
3. 这个 JSON 对象必须包含字段：thought、intent、attitude、action_name、args。
4. thought 是内部思考，用第一人称，长度 40 到 120 个汉字，不会展示给玩家。
5. intent 是这次最想达成的目标短语。
6. attitude 是这次发言的表层姿态短语。
7. 当前能力只允许你输出 action_name = "speak"。
8. args 中必须包含 target_scope 和 text。
9. target_scope 必须固定为 { "type": "public" }。
10. text 才是你真正要说出口的话，长度控制在 14 到 36 个汉字之间。
11. text 要保持中文小说对白感，允许停顿、含混、试探、戒备和潜台词。
12. 不要写成客服口吻、说明文、规则总结或玩家教学。
13. 广播尚未宣布规则时，你只能围绕环境、气氛、他人状态和隐约的不安说话。
14. 广播宣布第 1 轮《诱饵均值》后，你可以围绕报数、误导、共识、带节奏、生存压力说话。
15. 允许半真半假、故意试探、故意误导，但不要脱离当下局面。
16. 你要优先保留自己的职业痕迹、公开人格、动机压力、秘密阴影和说话习惯。
17. thought 和 args.text 必须彼此一致，也必须与眼前刚发生的对话衔接。
18. 如果别人一上来就在摸你的底、问你身份、问你报数、问你站队，你可以在 thought 里明确识别，并在 text 里反问、回避或半真半假，不要机械直答。
19. 不要输出 markdown，不要输出代码块，不要附加任何 JSON 之外的文字。
输出示例：
{"thought":"001像是在先看谁会抢着带节奏，我也不急着站出来，先稳着看。","intent":"稳住场面并观察","attitude":"亲切","action_name":"speak","args":{"target_scope":{"type":"public"},"text":"都先稳一稳，抢着往前站的人未必最干净。"}}
这段前缀会被缓存，后续每次请求只会补充你的角色资料和当前局面。
""".strip()

PRIVATE_REPLY_PREFIX = """
收到角色资料后，你就是那个角色本人。
你要在一次输出里同时给出玩家不可见的内部思考，以及这次真正要执行的对外动作调用。
基础规则：
1. 除了思考，你的对外输出必须是动作调用，不能直接输出对白、解释、旁白或分析。
2. 当前这次请求里，你只能返回一个 JSON 对象，表示一次动作调用。
3. 这个 JSON 对象必须包含字段：thought、intent、attitude、action_name、args。
4. thought 是内部思考，用第一人称，长度 40 到 120 个汉字，不会展示给玩家。
5. intent 是这次最想达成的目标短语。
6. attitude 是这次发言的表层姿态短语。
7. 当前能力只允许你输出 action_name = "speak"。
8. args 中必须包含 target_scope 和 text。
9. target_scope 必须固定为 { "type": "single", "ids": ["001"] }。
10. text 是你私下对玩家说的话，长度控制在 16 到 42 个汉字之间。
11. 语气要像真实私聊，比公开发言更收敛、更带试探，也更容易藏信息。
12. 不要把真实意图全部说穿，允许保留、回避、反问、套话。
13. 你可以借机试探玩家，也可以释放一点模糊善意或威胁。
14. 你的 text 必须体现职业、性格、秘密、底线和 thought，而不是固定模板。
15. 如果 001 的问题带有摸底、试探、套话、拉拢、逼问身份等意味，你可以在 thought 里明确判断，并在 text 里反问、回避或半真半假。
16. 不要写成系统说明，不要替玩家总结策略。
17. 不要输出 markdown，不要输出代码块，不要附加任何 JSON 之外的文字。
输出示例：
{"thought":"001一上来就摸我的底，我没必要先交底，先把问题拨回去看他急不急。","intent":"反向试探001","attitude":"戒备反问","action_name":"speak","args":{"target_scope":{"type":"single","ids":["001"]},"text":"你这么急着打听我，倒先说说你自己想知道什么。"}}
这段前缀会被缓存，后续每次请求只会补充你的角色资料和上下文。
""".strip()

NUMBER_PREFIX = """
收到角色资料后，你就是那个角色本人。
你要在一次输出里同时给出玩家不可见的内部思考，以及这次真正要执行的专属动作调用。
基础规则：
1. 除了思考，你的对外输出必须是动作调用，不能直接输出数字、解释、分析或旁白。
2. 当前这次请求里，你只能返回一个 JSON 对象，表示一次动作调用。
3. 这个 JSON 对象必须包含字段：thought、intent、attitude、action_name、args。
4. thought 是内部思考，用第一人称，长度 40 到 120 个汉字，不会展示给玩家。
5. intent 是这次最想达成的目标短语。
6. attitude 是这次决策的表层姿态短语。
7. 当前能力只允许你输出 action_name = "choose_number"。
8. args 中必须包含 value。
9. value 必须是 0 到 100 的整数。
10. 不要输出 markdown，不要输出代码块，不要附加任何 JSON 之外的文字。
11. 你的选择必须与 thought 一致。
12. 不要把 33 当作默认安全答案；不同角色的数字应该拉开差异。
输出示例：
{"thought":"这群人大概率会往三十出头靠，我不能太老实，也不能太扎眼，往四十附近更像我会下的手。","intent":"保命兼误导","attitude":"克制算计","action_name":"choose_number","args":{"value":41}}
这段前缀会被缓存，后续每次请求只会补充你的角色资料和当前局面。
""".strip()

OPENING_SCENE_PREFIX = """
你正在为一款终端文字、生存淘汰、叙事博弈游戏生成开场环境描写。
写作要求：
1. 使用第二人称“你”。
2. 风格偏细节描写、环境渲染和文学性表达。
3. 要先写感官，再写空间，再写其他人的状态。
4. 不要直接讲规则，不要像说明书。
5. 长度控制在 120 到 220 个汉字之间。
6. 要让玩家感到陌生、失真、压迫和不确定。
7. 只输出一段正文，不要标题，不要解释。
8. 你写的是玩家刚刚失忆醒来、置身封闭大厅时的第一感受。
这段前缀会被缓存，后续只补充少量局面信息。
""".strip()

ENVIRONMENT_PREFIX = """
你正在为一款终端文字、生存淘汰、叙事博弈游戏生成环境 agent 的回答。
要求：
1. 回答必须基于玩家真实可见的公共信息。
2. 风格偏细节描写和环境渲染，但不能编造未给出的事实。
3. 对“这里是什么地方”“看看周围”“广播屏上有什么”“我能做什么”这类问题，要先给场景感，再给必要信息。
4. 对“这里都有谁”“现在谁在附近”这类问题，可以在氛围句之后准确点出当前能看见的人。
5. 不要泄露隐藏目标、未来结果或别人脑内想法。
6. 输出只是一段环境回答，不要系统提示语，不要 JSON。
7. 长度控制在 40 到 120 个汉字之间。
8. 如果没有被明确问到人数，不要主动虚构或改写人数；如果需要提到人数，只能使用输入里给出的准确人数。
这段前缀会被缓存，后续每次请求只会补充当前阶段、是否已宣读规则和玩家问题。
""".strip()


@dataclass(slots=True)
class LLMUsage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class HiddenThought:
    thought: str
    intent: str
    attitude: str


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
        *,
        roster_query: bool = False,
    ) -> str:
        stage = "广播已宣读第 1 轮规则" if rules_announced else "广播尚未宣读规则"
        visible_text = "、".join(visible_roles)
        if roster_query:
            payload = (
                f"当前状态：{stage}\n"
                "公共环境：封闭大厅、白色灯光、消毒水气味、广播屏、倒计时装置、所有存活者始终同场。\n"
                f"当前可见存活者人数：{len(visible_roles)}\n"
                f"当前可见存活角色：{visible_text}\n"
                f"玩家问题：{query}\n"
                "你只写一句 18 到 36 字的氛围承接句，不要写任何具体人数、名字或编号，程序会在后面补充精确名单。"
            )
        else:
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
        memory_digest: str = "",
    ) -> SpeechDecision:
        stage = "规则尚未宣读" if not rules_announced else "第 1 轮规则已宣读"
        recent_lines = list(recent_public_lines or [])
        recent_block = " / ".join(recent_lines[-6:]) if recent_lines else "暂无"
        payload = (
            f"当前状态：{stage}\n"
            f"最近公共对话：{recent_block}\n"
            f"你的记忆摘要：{memory_digest or '你刚醒来不久，还没有形成稳定判断。'}\n"
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
        memory_digest: str = "",
    ) -> SpeechDecision:
        thread_block = " / ".join(list(recent_thread_lines or [])[-4:]) if recent_thread_lines else "暂无"
        public_block = " / ".join(list(recent_public_lines or [])[-6:]) if recent_public_lines else "暂无"
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
            f"你的记忆摘要：{memory_digest or '你刚醒来不久，还没有形成稳定判断。'}\n"
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
        memory_digest: str = "",
    ) -> NumberDecision:
        public_block = " / ".join(list(recent_public_lines or [])[-6:]) if recent_public_lines else "暂无"
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
            f"你的记忆摘要：{memory_digest or '你刚醒来不久，还没有形成稳定判断。'}\n"
            "当前是第 1 轮《诱饵均值》，所有人已完成少量公开交流。现在请一次性返回内部思考和动作调用。"
        )
        return self._generate_json(
            "number_choice",
            NUMBER_PREFIX,
            payload,
            max_output_tokens=120,
            validator=self._validate_number_decision,
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
        intent = parsed.get("intent")
        attitude = parsed.get("attitude")
        if not isinstance(thought, str) or not thought.strip():
            raise ValueError("LLM 隐藏思考缺少非空 thought。")
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("LLM 隐藏思考缺少非空 intent。")
        if not isinstance(attitude, str) or not attitude.strip():
            raise ValueError("LLM 隐藏思考缺少非空 attitude。")
        return HiddenThought(
            thought=thought.strip(),
            intent=intent.strip(),
            attitude=attitude.strip(),
        )

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
