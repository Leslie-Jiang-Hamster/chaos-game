from __future__ import annotations

import json
import mimetypes
import random
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from chaos.config import load_model_config
from chaos.llm import ArkResponsesClient
from chaos.models import Message, Role, RoundOneResult
from chaos.role_loader import load_roles
from chaos.round_one import RoundOneGame
from chaos.runtime_log import RuntimeLogger


DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "角色池设定.md"
KEY_PATH = Path(__file__).resolve().parent.parent / "key.yaml"
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "chaos_runtime.jsonl"
STATIC_DIR = Path(__file__).resolve().parent.parent / "web"
TOTAL_CONTESTANTS = 10
ROUND_ONE_ELIMINATION_COUNT = 2


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


def _build_llm_client() -> ArkResponsesClient:
    try:
        config = load_model_config(KEY_PATH)
    except Exception as exc:
        raise RuntimeError(f"[LLM] 初始化失败，未能读取 key.yaml 或模型配置非法：{exc}") from exc
    return ArkResponsesClient(config=config, logger=RuntimeLogger(LOG_PATH))


@dataclass(slots=True)
class WebGameSession:
    host: str = "127.0.0.1"
    port: int = 8000
    rng: random.Random = field(default_factory=lambda: random.Random())
    lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    scheduler_stop: threading.Event = field(default_factory=threading.Event, init=False)
    scheduler_thread: threading.Thread | None = field(default=None, init=False)
    game: RoundOneGame = field(init=False)
    player: Role = field(init=False)
    contestants: list[Role] = field(init=False)
    phase_id: str = field(default="free_social", init=False)
    phase_label: str = field(default="自由社交阶段", init=False)
    phase_deadline_seconds: int = field(default=180, init=False)
    phase_started_at: float = field(default_factory=time.time, init=False)
    player_has_chosen: bool = field(default=False, init=False)
    result: RoundOneResult | None = field(default=None, init=False)
    next_auto_public_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._reset_locked()
        self.scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="chaos-web-scheduler",
            daemon=True,
        )
        self.scheduler_thread.start()

    def close(self) -> None:
        self.scheduler_stop.set()
        if self.scheduler_thread is not None:
            self.scheduler_thread.join(timeout=1.5)

    def reset(self) -> None:
        with self.lock:
            self._reset_locked()

    def bootstrap_payload(self) -> dict[str, Any]:
        with self.lock:
            messages = [self._serialize_message(message) for message in self.game.messages]
            return {
                "messages": messages,
                "cursor": self._current_cursor(),
                "conversations": self._serialize_conversations(),
                "state": self._serialize_state(),
                "player": self._serialize_role(self.player),
            }

    def delta_payload(self, cursor: int) -> dict[str, Any]:
        with self.lock:
            messages = [
                self._serialize_message(message)
                for message in self.game.messages
                if message.message_id > cursor
            ]
            return {
                "messages": messages,
                "cursor": self._current_cursor(),
                "conversations": self._serialize_conversations(),
                "state": self._serialize_state(),
            }

    def send_message(self, conversation_id: str, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("发言内容不能为空。")
        with self.lock:
            if self.phase_id == "resolved":
                raise ValueError("游戏已经结算，不能继续发言。")
            before = self._current_cursor()
            if conversation_id == "lobby":
                self.game.player_public_speak(cleaned)
                self._append_llm_public_replies(trigger_text=cleaned, trigger_speaker_id=self.player.role_id)
            elif conversation_id.startswith("private:"):
                target_id = conversation_id.split(":", 1)[1]
                if target_id == "environment":
                    raise ValueError("环境 agent 不支持私聊。")
                if target_id == "broadcast":
                    self.game.player_host_private_speak(cleaned)
                    self.game.host_private_message()
                else:
                    self.game.player_private_speak(target_id, cleaned)
                    self._append_llm_private_reply(target_id)
            else:
                raise ValueError(f"未知会话: {conversation_id}")
            return self.delta_payload(before)

    def choose_number(self, value: int) -> dict[str, Any]:
        with self.lock:
            if self.phase_id != "round_execution":
                raise ValueError("主持人还没宣读规则，现在不能提交数字。")
            if self.player_has_chosen:
                raise ValueError("你已经提交过数字了。")
            if not 0 <= value <= 100:
                raise ValueError("数字必须在 0 到 100 之间。")
            self.game.submit_player_number(value)
            self.player_has_chosen = True
            return {
                "ok": True,
                "state": self._serialize_state(),
            }

    def end_phase(self) -> dict[str, Any]:
        with self.lock:
            before = self._current_cursor()
            if self.phase_id == "free_social":
                self._transition_to_execution()
            elif self.phase_id == "round_execution":
                if not self.player_has_chosen:
                    raise ValueError("这一阶段结束前，你还没有提交数字。")
                self._resolve_round()
            else:
                raise ValueError("当前阶段已经结束。")
            return self.delta_payload(before)

    def _reset_locked(self) -> None:
        if LOG_PATH.exists():
            LOG_PATH.unlink()
        roles = load_roles(DOC_PATH)
        if len(roles) < TOTAL_CONTESTANTS:
            raise RuntimeError(f"角色池数量不足：至少需要 {TOTAL_CONTESTANTS} 人，当前仅 {len(roles)} 人。")
        self.player = _build_player()
        npc_count = TOTAL_CONTESTANTS - 1
        npc_roles = [role for role in roles if role.role_id != self.player.role_id][:npc_count]
        if len(npc_roles) < npc_count:
            raise RuntimeError(f"可用 NPC 数量不足：至少需要 {npc_count} 人，当前仅 {len(npc_roles)} 人。")
        self.contestants = [self.player, *npc_roles]
        llm = _build_llm_client()
        self.game = RoundOneGame(
            player=self.player,
            contestants=self.contestants,
            llm=llm,
            elimination_count=ROUND_ONE_ELIMINATION_COUNT,
        )
        self.phase_id = "free_social"
        self.phase_label = "自由社交阶段"
        self.phase_deadline_seconds = 180
        self.phase_started_at = time.time()
        self.player_has_chosen = False
        self.result = None
        self.next_auto_public_at = time.time() + self.rng.uniform(8.0, 12.0)
        self._seed_initial_messages()

    def _seed_initial_messages(self) -> None:
        self.game.opening_environment_message()
        self._append_seed_social_messages()

    def _transition_to_execution(self) -> None:
        self.game.execution_environment_message()
        self.game.broadcast_round_intro_message()
        self.phase_id = "round_execution"
        self.phase_label = "第 1 轮执行阶段"
        self.phase_deadline_seconds = 240
        self.phase_started_at = time.time()
        self.next_auto_public_at = time.time() + self.rng.uniform(7.0, 11.0)
        self._append_seed_social_messages()

    def _resolve_round(self) -> None:
        self.game.auto_submit_npc_numbers()
        self.result = self.game.resolve()
        self.phase_id = "resolved"
        self.phase_label = "第 1 轮已结算"
        self.phase_deadline_seconds = 0
        self.phase_started_at = time.time()
        self.game.resolved_environment_message()
        self._append_result_messages(self.result)

    def _append_result_messages(self, result: RoundOneResult) -> None:
        top_survivors = "、".join(
            f"{role.role_id} {role.name}"
            for role in result.survivors[:8]
        )
        player_alive = any(role.role_id == self.player.role_id for role in result.survivors)
        outcome = "你活过了第 1 轮。MVP 到此结束。" if player_alive else "你在第 1 轮被淘汰。MVP 到此结束。"
        messages = [
            Message(
                speaker_id="broadcast",
                speaker_name="主持人",
                text=f"第 1 轮结算完成。全体真实平均值为 {result.average:.2f}，目标数为 {result.target:.2f}。",
                visibility="public",
                source="system",
                recipients=[role.role_id for role in self.contestants],
            ),
            Message(
                speaker_id="broadcast",
                speaker_name="主持人",
                text=f"本轮 {len(result.survivors)} 人存活，包括：{top_survivors}。",
                visibility="public",
                source="system",
                recipients=[role.role_id for role in self.contestants],
            ),
            Message(
                speaker_id="broadcast",
                speaker_name="主持人",
                text=outcome,
                visibility="public",
                source="system",
                recipients=[role.role_id for role in self.contestants],
            ),
        ]
        for message in messages:
            self.game._append_public_message(message)

    def _append_seed_social_messages(self) -> None:
        self.game.seed_social_phase()

    def _append_llm_public_replies(self, *, trigger_text: str, trigger_speaker_id: str) -> None:
        self.game.npc_public_replies(
            trigger_text=trigger_text,
            trigger_speaker_id=trigger_speaker_id,
        )

    def _append_llm_private_reply(self, target_id: str) -> None:
        self.game.npc_private_reply(target_id)

    def _scheduler_loop(self) -> None:
        while not self.scheduler_stop.is_set():
            time.sleep(0.5)
            with self.lock:
                if self.phase_id not in {"free_social", "round_execution"}:
                    continue
                now = time.time()
                if now < self.next_auto_public_at:
                    continue
                self._append_seed_social_messages()
                self.next_auto_public_at = now + self.rng.uniform(8.0, 14.0)

    def _current_cursor(self) -> int:
        if not self.game.messages:
            return 0
        return self.game.messages[-1].message_id

    def _conversation_id_for(self, message: Message) -> str:
        if message.visibility == "public":
            return "lobby"
        other_ids = sorted(role_id for role_id in message.recipients if role_id != self.player.role_id)
        if other_ids:
            return f"private:{other_ids[0]}"
        return "lobby"

    def _serialize_message(self, message: Message) -> dict[str, Any]:
        conversation_id = self._conversation_id_for(message)
        return {
            "id": message.message_id,
            "conversation_id": conversation_id,
            "speaker_id": message.speaker_id,
            "speaker_name": message.speaker_name,
            "text": message.text,
            "visibility": message.visibility,
            "source": message.source,
            "recipients": message.recipients,
            "created_at": message.created_at,
            "in_lobby": message.visibility == "public",
            "is_player": message.speaker_id == self.player.role_id,
        }

    def _serialize_conversations(self) -> list[dict[str, Any]]:
        conversations: list[dict[str, Any]] = [
            {
                "id": "lobby",
                "title": "公共大厅",
                "kind": "public",
                "summary": self._conversation_summary("lobby"),
            },
            {
                "id": "private:broadcast",
                "title": "主持人（私聊）",
                "kind": "private",
                "summary": self._conversation_summary("private:broadcast"),
            },
        ]
        for role in self.contestants:
            if role.is_player:
                continue
            conversations.append(
                {
                    "id": f"private:{role.role_id}",
                    "title": f"{role.role_id} {role.name}",
                    "kind": "private",
                    "summary": self._conversation_summary(f"private:{role.role_id}"),
                    "role": self._serialize_role(role),
                }
            )
        return conversations

    def _conversation_summary(self, conversation_id: str) -> str:
        messages = self._messages_for_conversation(conversation_id)
        if not messages:
            if conversation_id.startswith("private:"):
                return "尚无私聊"
            return "所有公开消息会在这里汇集"
        return messages[-1].text[:40]

    def _messages_for_conversation(self, conversation_id: str) -> list[Message]:
        if conversation_id == "lobby":
            return [message for message in self.game.messages if message.visibility == "public"]
        if conversation_id.startswith("private:"):
            role_id = conversation_id.split(":", 1)[1]
            return self.game.private_messages_for(role_id)
        return []

    def _serialize_state(self) -> dict[str, Any]:
        remaining = max(0, int(self.phase_deadline_seconds - (time.time() - self.phase_started_at)))
        return {
            "phase_id": self.phase_id,
            "phase_label": self.phase_label,
            "rules_announced": self.game.rules_announced,
            "alive_count": len(self.game.list_alive()),
            "player_has_chosen": self.player_has_chosen,
            "remaining_seconds": remaining,
            "can_choose_number": self.phase_id == "round_execution" and not self.player_has_chosen,
            "can_end_phase": self.phase_id in {"free_social", "round_execution"},
            "rules_summary": (
                f"先公开交流，再秘密提交 0-100 的整数。目标数为全体真实平均值的一半，距离目标最近的前 {self.game.survivor_quota()} 名存活。"
                if self.game.rules_announced
                else "主持人尚未宣读第 1 轮规则。你现在处于自由社交阶段。"
            ),
            "result": self._serialize_result(self.result),
        }

    def _serialize_result(self, result: RoundOneResult | None) -> dict[str, Any] | None:
        if result is None:
            return None
        ranking_map = {
            role.role_id: (value, distance)
            for role, value, distance in result.rankings
        }
        player_alive = any(role.role_id == self.player.role_id for role in result.survivors)
        return {
            "average": round(result.average, 2),
            "target": round(result.target, 2),
            "player_alive": player_alive,
            "survivors": [
                {
                    "role_id": role.role_id,
                    "name": role.name,
                    "value": ranking_map[role.role_id][0],
                    "distance": round(ranking_map[role.role_id][1], 2),
                }
                for role in result.survivors
            ],
            "eliminated": [
                {
                    "role_id": role.role_id,
                    "name": role.name,
                    "value": ranking_map[role.role_id][0],
                    "distance": round(ranking_map[role.role_id][1], 2),
                }
                for role in result.eliminated
            ],
        }

    def _serialize_role(self, role: Role) -> dict[str, Any]:
        return {
            "role_id": role.role_id,
            "name": role.name,
            "age_job": role.age_job,
            "background": role.background,
            "public_persona": role.public_persona,
            "motive": role.motive,
            "core_trait": role.core_trait,
        }


class SessionManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session = WebGameSession()

    def close(self) -> None:
        with self._lock:
            self._session.close()

    def bootstrap_payload(self) -> dict[str, Any]:
        with self._lock:
            return self._session.bootstrap_payload()

    def delta_payload(self, cursor: int) -> dict[str, Any]:
        with self._lock:
            return self._session.delta_payload(cursor)

    def send_message(self, conversation_id: str, text: str) -> dict[str, Any]:
        with self._lock:
            return self._session.send_message(conversation_id, text)

    def choose_number(self, value: int) -> dict[str, Any]:
        with self._lock:
            return self._session.choose_number(value)

    def end_phase(self) -> dict[str, Any]:
        with self._lock:
            return self._session.end_phase()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            old = self._session
            self._session = WebGameSession()
            old.close()
            return self._session.bootstrap_payload()


class ChaosHTTPRequestHandler(BaseHTTPRequestHandler):
    server_version = "ChaosHTTP/0.1"

    @property
    def session_manager(self) -> SessionManager:
        return self.server.session_manager  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._serve_file("index.html")
                return
            if parsed.path.startswith("/static/"):
                self._serve_file(parsed.path.removeprefix("/static/"))
                return
            if parsed.path == "/api/bootstrap":
                self._safe_write_json(HTTPStatus.OK, self.session_manager.bootstrap_payload())
                return
            if parsed.path == "/api/state":
                query = parse_qs(parsed.query)
                raw_cursor = query.get("cursor", ["0"])[0]
                try:
                    cursor = int(raw_cursor or 0)
                except (TypeError, ValueError):
                    self._safe_write_json(HTTPStatus.BAD_REQUEST, {"error": "cursor 必须是非负整数。"})
                    return
                if cursor < 0:
                    self._safe_write_json(HTTPStatus.BAD_REQUEST, {"error": "cursor 必须是非负整数。"})
                    return
                self._safe_write_json(HTTPStatus.OK, self.session_manager.delta_payload(cursor))
                return
            self._safe_write_json(HTTPStatus.NOT_FOUND, {"error": "未找到资源。"})
        except Exception as exc:
            if self._is_client_disconnect(exc):
                return
            self._safe_write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"{exc.__class__.__name__}: {exc}"},
            )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
            if parsed.path == "/api/send":
                data = self.session_manager.send_message(
                    str(payload.get("conversation_id", "")),
                    str(payload.get("text", "")),
                )
                self._safe_write_json(HTTPStatus.OK, data)
                return
            if parsed.path == "/api/choose-number":
                raw_value = payload.get("value")
                if not isinstance(raw_value, int):
                    raise ValueError("数字必须是整数。")
                data = self.session_manager.choose_number(raw_value)
                self._safe_write_json(HTTPStatus.OK, data)
                return
            if parsed.path == "/api/end-phase":
                data = self.session_manager.end_phase()
                self._safe_write_json(HTTPStatus.OK, data)
                return
            if parsed.path == "/api/reset":
                data = self.session_manager.reset()
                self._safe_write_json(HTTPStatus.OK, data)
                return
        except ValueError as exc:
            self._safe_write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:
            if self._is_client_disconnect(exc):
                return
            self._safe_write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"{exc.__class__.__name__}: {exc}"},
            )
            return
        self._safe_write_json(HTTPStatus.NOT_FOUND, {"error": "未找到资源。"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"请求体 JSON 非法：{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象。")
        return payload

    def _serve_file(self, relative_path: str) -> None:
        file_path = (STATIC_DIR / relative_path).resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())) or not file_path.exists():
            self._safe_write_json(HTTPStatus.NOT_FOUND, {"error": "静态资源不存在。"})
            return
        content_type, _ = mimetypes.guess_type(file_path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _safe_write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        try:
            self._write_json(status, payload)
        except Exception as exc:
            if self._is_client_disconnect(exc):
                return
            raise

    def _is_client_disconnect(self, exc: BaseException) -> bool:
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return True
        if isinstance(exc, OSError) and exc.errno in {32, 104, 10053, 10054}:
            return True
        return False


class ChaosHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, ChaosHTTPRequestHandler)
        self.session_manager = SessionManager()

    def server_close(self) -> None:
        self.session_manager.close()
        super().server_close()


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ChaosHTTPServer((host, port))
    url = f"http://{host}:{port}"
    print(f"[Chaos] Web 服务已启动：{url}")
    print("[Chaos] 打开浏览器访问上面的地址。按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Chaos] 服务已停止。")
    finally:
        server.server_close()
