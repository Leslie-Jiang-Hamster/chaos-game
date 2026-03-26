from __future__ import annotations

from pathlib import Path

from chaos.config import load_model_config
from chaos.llm import ArkResponsesClient
from chaos.models import Message, Role
from chaos.role_loader import load_roles
from chaos.round_one import LLMActionError, RoundOneGame
from chaos.runtime_log import RuntimeLogger


DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "角色池设定.md"
KEY_PATH = Path(__file__).resolve().parent.parent / "key.yaml"
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "chaos_runtime.jsonl"


def run() -> None:
    if LOG_PATH.exists():
        LOG_PATH.unlink()
    roles = load_roles(DOC_PATH)
    player = _build_player()
    contestants = [player, *roles[1:]]
    llm = _build_llm_client()
    if llm is None:
        return
    game = RoundOneGame(player=player, contestants=contestants, llm=llm)

    try:
        opening_message = game.opening_environment_message()
        print(_render_message(opening_message))
    except LLMActionError as exc:
        print(str(exc))
    _print_help_overview(pre_game=True)

    try:
        seeded_messages = game.seed_social_phase()
        for message in seeded_messages:
            print(_render_message(message))
    except LLMActionError as exc:
        print(str(exc))

    _social_phase(game, phase_name="自由社交阶段", allow_choose=False)

    print("\n【广播开始】")
    print(_render_message(game.broadcast_round_intro_message()))
    _print_help_overview(pre_game=False)

    print("\n【规则宣读后的公开讨论】")
    try:
        seeded_messages = game.seed_social_phase()
        for message in seeded_messages:
            print(_render_message(message))
    except LLMActionError as exc:
        print(str(exc))

    _social_phase(game, phase_name="第 1 轮执行阶段", allow_choose=True)
    result = game.resolve()
    _print_result(result)


def _build_player() -> Role:
    return Role(
        role_id="001",
        name="你",
        age_job="未知",
        background="你失去了进入这里之前的大部分记忆。睁开眼时，人已经在这处封闭大厅里，只知道自己必须活下去。",
        public_persona="谨慎观察",
        motive="活下去并弄清自己为什么会在这里",
        core_trait="观察、试探、记忆",
        secret="失忆前经历不明",
        taboo="无法确认",
        is_player=True,
    )


def _build_llm_client() -> ArkResponsesClient | None:
    try:
        config = load_model_config(KEY_PATH)
    except Exception as exc:
        print(f"\n[LLM] 未能读取 key.yaml，程序不会退回本地规则化模式：{exc}")
        return None

    print(f"\n[LLM] 已加载火山引擎模型：{config.display_name} ({config.model})")
    print("[LLM] 环境描写、NPC 公开发言、私聊回复和数字选择将直接使用 Responses API，并启用缓存。")
    print(f"[LLM] 运行日志将写入：{LOG_PATH}\n")
    return ArkResponsesClient(config=config, logger=RuntimeLogger(LOG_PATH))


def _print_help_overview(pre_game: bool) -> None:
    print("\n【帮助提示】")
    print("世界内基础动作只有两个：`speak` （说话）和 `end`（结束本阶段）。")
    print("如果你一开始不知道该做什么，最稳妥的做法是先对环境agent (environment)开口，它会告诉回答你一些关于公开信息的问题。")
    print("你可以这样输入：")
    print("speak environment 周围")
    print("speak public 现在谁在看？")
    print("speak 004 你是谁")
    if pre_game:
        print("end")
        print("输入 `help` 可以再次查看提示。正式游戏还没开始，先观察通常比急着表态更安全。")
    else:
        print("choose 33")
        print("end")
        print("输入 `help` 可以再次查看提示。现在广播已经宣读规则，你可以先试探，再决定数字。")
    print('')


def _social_phase(game: RoundOneGame, phase_name: str, allow_choose: bool) -> None:
    print(f"\n【{phase_name}】")
    player_has_chosen = False
    while True:
        raw = input("> ").strip()
        if not raw:
            continue
        if raw == "help":
            _print_help_overview(pre_game=not allow_choose)
            continue
        if raw == "end":
            if allow_choose and not player_has_chosen:
                print("这一阶段结束前，你还没有提交数字。请先使用 choose <0-100>。")
                continue
            if allow_choose:
                try:
                    game.auto_submit_npc_numbers()
                except LLMActionError as exc:
                    print(str(exc))
                    continue
            break
        if raw.startswith("choose "):
            if not allow_choose:
                print("正式游戏还没开始，现在不能提交数字。")
                continue
            if player_has_chosen:
                print("你已经提交过数字了。")
                continue
            ok = _handle_choose(game, raw)
            if ok:
                player_has_chosen = True
            continue
        if raw.startswith("speak "):
            _handle_speak(game, raw)
            continue
        print("未知输入。当前可用：speak / end" + (" / choose / help" if allow_choose else " / help"))


def _handle_speak(game: RoundOneGame, raw: str) -> None:
    parts = raw.split(maxsplit=2)
    if len(parts) < 3:
        print("用法: speak public <内容> / speak environment <内容> / speak <角色编号> <内容>")
        return
    _, target, text = parts
    text = text.strip()
    if not text:
        print("发言内容不能为空。")
        return
    if target == "public":
        message = game.player_public_speak(text)
        print(_render_message(message))
        try:
            for reply in game.npc_public_replies(trigger_text=text, trigger_speaker_id=message.speaker_id):
                print(_render_message(reply))
        except LLMActionError as exc:
            print(str(exc))
        return
    if target in {"environment", "env"}:
        print(f"[私聊] {_render_message(game.player_environment_speak(text))}")
        try:
            reply = game.environment_message(text)
            print(f"[私聊] {_render_message(reply)}")
        except LLMActionError as exc:
            print(str(exc))
        return
    try:
        sent = game.player_private_speak(target, text)
        reply = game.npc_private_reply(target)
    except ValueError as exc:
        print(str(exc))
        return
    except LLMActionError as exc:
        print(f"[私聊] {_render_message(sent)}")
        print(str(exc))
        return
    print(f"[私聊] {_render_message(sent)}")
    print(f"[私聊] {_render_message(reply)}")


def _handle_choose(game: RoundOneGame, raw: str) -> bool:
    value_str = raw.split(maxsplit=1)[1]
    try:
        value = int(value_str)
    except ValueError:
        print("数字必须是整数。")
        return False
    if not 0 <= value <= 100:
        print("数字必须在 0 到 100 之间。")
        return False
    game.submit_player_number(value)
    print(f"你已秘密提交数字：{value}")
    return True


def _print_result(result) -> None:
    print("\n【结算】")
    print(f"全体真实平均值: {result.average:.2f}")
    print(f"目标数: {result.target:.2f}")

    print("\n前 16 名存活者:")
    for index, (role, value, distance) in enumerate(result.rankings[:16], start=1):
        print(f"{index:02d}. {role.role_id} {role.name} 提交 {value}，距离 {distance:.2f}")

    print("\n淘汰者:")
    for role, value, distance in result.rankings[16:]:
        print(f"- {role.role_id} {role.name} 提交 {value}，距离 {distance:.2f}")

    player_alive = any(role.role_id == "001" for role in result.survivors)
    print("\n【结果判定】")
    if player_alive:
        print("你活过了第 1 轮。MVP 到此结束。")
    else:
        print("你在第 1 轮被淘汰。MVP 到此结束。")


def _source_tag(source: str) -> str:
    if source in {"llm", "player"}:
        return ""
    return "[system]"


def _render_message(message: Message) -> str:
    prefix = _source_tag(message.source)
    if prefix:
        return f"{prefix} {message.speaker_id} {message.speaker_name}: {message.text}"
    return f"{message.speaker_id} {message.speaker_name}: {message.text}"
