"""基于 agent-wechat 1.x (/v1) SSE 事件流与增量游标的个人微信平台适配器。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from contextlib import suppress
from datetime import datetime
from typing import Any, Awaitable, Callable, cast

from astrbot.api import logger
from astrbot.api.message_components import (
    At,
    File,
    Image,
    Location,
    Plain,
    Record,
    Reply,
    Share,
    Video,
)
from astrbot.api.platform import (
    AstrBotMessage,
    Group,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.core.platform.astr_message_event import MessageSesion

from .agent_wechat_access import (
    is_group_chat,
    is_native_mentioned,
    is_official_account,
    normalize_allowlist,
    should_forward_message,
    strip_leading_mentions,
)
from .agent_wechat_client import (
    AgentWeChatAPIError,
    WeChatClient,
    WeChatEventStreamClient,
)
from .agent_wechat_event import AgentWeChatMessageEvent

MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 3
MSG_TYPE_VOICE = 34
MSG_TYPE_VIDEO = 43
MSG_TYPE_STICKER = 47
MSG_TYPE_LOCATION = 48
MSG_TYPE_APP = 49

SUPPORTED_MSG_TYPES = {
    MSG_TYPE_TEXT,
    MSG_TYPE_IMAGE,
    MSG_TYPE_VOICE,
    MSG_TYPE_VIDEO,
    MSG_TYPE_STICKER,
    MSG_TYPE_LOCATION,
    MSG_TYPE_APP,
}
MEDIA_TYPES = {
    MSG_TYPE_IMAGE,
    MSG_TYPE_VOICE,
    MSG_TYPE_VIDEO,
    MSG_TYPE_STICKER,
    MSG_TYPE_APP,
}

POLL_INTERVAL_MS = 1000
EVENT_SYNC_INTERVAL_MS = 3000
AUTH_POLL_INTERVAL_MS = 30000
LOGIN_PAGE_WARN_INTERVAL_SECONDS = 60.0
HOT_PATH_TIMEOUT_SECONDS = 10.0
MEDIA_WAIT_TIMEOUT_SECONDS = 25.0
SELF_ID_ALIAS_RE = re.compile(r"^(wxid_[^_]+)(?:_[0-9a-fA-F]{4,})$")

CONFIG_METADATA = {
    "en-US": {
        "server_url": {
            "label": "服务地址",
            "help_text": (
                "agent-wechat (1.x) 的 HTTP/HTTPS API 地址，例如 "
                "http://192.168.1.100:6174 或 https://192.168.1.100:7443。"
            ),
            "field_type": "str",
        },
        "token": {
            "label": "访问令牌 (Bearer Token)",
            "help_text": (
                "必填。在部署 agent-wechat 的服务器上读取 "
                "~/.local/share/agent-wechat/api-token 或通过 wx tokens create 创建。"
            ),
            "field_type": "str",
            "secret": True,
        },
        "account_id": {
            "label": "微信账号 ID (accountId)",
            "help_text": (
                "可选。执行 wx accounts list 查看 id 或 alias；留空则自动选择第一个已登录账号。"
            ),
            "field_type": "str",
        },
        "verify_tls": {
            "label": "校验 HTTPS 证书",
            "help_text": "跨服务器使用自签名证书连接 agent-wechat networkListen (7443) 时可设为 false。",
            "field_type": "bool",
        },
        "read_policy": {
            "label": "已读回执策略",
            "help_text": "on_inbound: 收到并投递给 AstrBot 后标记会话已读；never: 不修改微信未读状态。",
            "field_type": "str",
            "options": ["on_inbound", "never"],
        },
        "require_group_mention": {
            "label": "群聊仅在 @机器人 时唤醒",
            "help_text": "开启后群聊需 @机器人 才会触发大模型回复；关闭则群内所有消息均触发。",
            "field_type": "bool",
        },
        "dm_policy": {
            "label": "私聊访问策略",
            "help_text": "open: 允许所有私聊；allowlist: 仅允许白名单；disabled: 禁用私聊。",
            "field_type": "str",
            "options": ["open", "allowlist", "disabled"],
        },
        "dm_allowlist": {
            "label": "私聊白名单",
            "help_text": "当私聊策略为 allowlist 时生效，填写允许的 wxid 列表。",
            "field_type": "list",
        },
        "group_policy": {
            "label": "群聊访问策略",
            "help_text": "open: 允许所有群聊；allowlist: 仅允许白名单发送者/群；disabled: 禁用群聊。",
            "field_type": "str",
            "options": ["open", "allowlist", "disabled"],
        },
        "group_allowlist": {
            "label": "群聊白名单",
            "help_text": "当群聊策略为 allowlist 时生效，可填写群 ID (xxx@chatroom) 或发送者 wxid。",
            "field_type": "list",
        },
    }
}

DEFAULT_CONFIG = {
    "server_url": "http://127.0.0.1:6174",
    "token": "",
    "account_id": "",
    "verify_tls": False,
    "read_policy": "on_inbound",
    "require_group_mention": False,
    "dm_policy": "open",
    "dm_allowlist": [],
    "group_policy": "open",
    "group_allowlist": [],
}


def _parse_timestamp(value: str | int | float | None) -> int:
    if value is None or value == "":
        return int(time.time())
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 1_000_000_000_000 else int(value)
    try:
        return int(
            datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        )
    except ValueError:
        return int(time.time())


def _safe_temp_dir() -> str:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

        return get_astrbot_temp_path()
    except Exception:
        return tempfile.gettempdir()


def _safe_state_dir() -> str:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        path = os.path.join(
            get_astrbot_plugin_data_path(), "astrbot_agent_wechat_bridge"
        )
    except Exception:
        path = os.path.join(_safe_temp_dir(), "astrbot_agent_wechat_bridge_state")
    os.makedirs(path, exist_ok=True)
    return path


def _mime_to_component(path: str, mime_type: str, filename: str):
    lower_mime = (mime_type or "").lower()
    if lower_mime.startswith("image/"):
        return Image(file=path)
    if lower_mime.startswith("audio/"):
        return Record(file=path)
    if lower_mime.startswith("video/"):
        return Video(file=path)
    return File(name=filename or os.path.basename(path), file=path)


_PROCESS_ACTIVE_INSTANCES: list[Any] = getattr(
    sys, "_agent_wechat_active_instances", None
)
if _PROCESS_ACTIVE_INSTANCES is None:
    _PROCESS_ACTIVE_INSTANCES = []
    setattr(sys, "_agent_wechat_active_instances", _PROCESS_ACTIVE_INSTANCES)

_PROCESS_SEEN_IDS: set[str] = getattr(sys, "_agent_wechat_global_seen_ids", None)
if _PROCESS_SEEN_IDS is None:
    _PROCESS_SEEN_IDS = set()
    setattr(sys, "_agent_wechat_global_seen_ids", _PROCESS_SEEN_IDS)

_PROCESS_SEEN_ORDER: list[str] = getattr(sys, "_agent_wechat_global_seen_order", None)
if _PROCESS_SEEN_ORDER is None:
    _PROCESS_SEEN_ORDER = []
    setattr(sys, "_agent_wechat_global_seen_order", _PROCESS_SEEN_ORDER)


@register_platform_adapter(
    "agent_wechat",
    "WeChat adapter powered by agent-wechat (1.x Native API).",
    default_config_tmpl=DEFAULT_CONFIG,
    support_streaming_message=False,
    config_metadata=CONFIG_METADATA,
    adapter_display_name="Agent WeChat",
)
class AgentWeChatPlatformAdapter(Platform):
    """以 /v1 SSE 事件流为主触发，增量游标轮询补偿兜底。"""

    _active_instances: list["AgentWeChatPlatformAdapter"] = _PROCESS_ACTIVE_INSTANCES
    _global_seen_message_ids: set[str] = _PROCESS_SEEN_IDS
    _global_seen_order: list[str] = _PROCESS_SEEN_ORDER
    _logout_notifier: Callable[[str], Awaitable[None] | None] | None = None
    _logout_warn_interval_seconds: float = LOGIN_PAGE_WARN_INTERVAL_SECONDS
    _logout_warn_max_count: int = 0
    _logout_warn_sent_count: int = 0
    _logout_warn_limit_logged: bool = False

    @classmethod
    def get_active_instance(cls) -> "AgentWeChatPlatformAdapter | None":
        return cls._active_instances[-1] if cls._active_instances else None

    @classmethod
    def set_logout_notifier(
        cls,
        notifier: Callable[[str], Awaitable[None] | None] | None,
    ) -> None:
        cls._logout_notifier = notifier

    @classmethod
    def set_logout_notify_policy(cls, *, interval_seconds: float, max_count: int) -> None:
        cls._logout_warn_interval_seconds = max(1.0, float(interval_seconds))
        cls._logout_warn_max_count = max(0, int(max_count))

    @classmethod
    def reset_logout_warning_state(cls) -> None:
        cls._logout_warn_sent_count = 0
        cls._logout_warn_limit_logged = False

    def __init__(
        self,
        platform_config: dict[str, Any],
        platform_settings: dict[str, Any],
        event_queue: asyncio.Queue,
    ) -> None:
        try:
            super().__init__(platform_config, event_queue)
        except TypeError:
            super().__init__(event_queue)
            self.config = platform_config

        self.settings = platform_settings
        self.config = {**DEFAULT_CONFIG, **(platform_config or {})}
        self.metadata = PlatformMetadata(
            name="agent_wechat",
            description="WeChat adapter powered by agent-wechat (1.x Native API).",
            id=cast(str, self.config.get("id", "agent_wechat")),
            support_streaming_message=False,
        )
        account_id_cfg = str(self.config.get("account_id") or "").strip() or None
        verify_tls_cfg = bool(self.config.get("verify_tls", False))
        self.client = WeChatClient(
            base_url=str(self.config["server_url"]),
            token=str(self.config.get("token") or "") or None,
            account_id=account_id_cfg,
            verify_tls=verify_tls_cfg,
        )
        self.shutdown_event = asyncio.Event()
        self.sync_event = asyncio.Event()
        self.event_cursor: str = "0"
        self.server_id: str | None = None
        self.consumer_id = f"astrbot-{self.metadata.id}"
        self.event_lock = asyncio.Lock()
        self.seen_message_ids: set[str] = set()
        self.in_flight_message_ids: set[str] = set()
        self._seen_message_order: list[str] = []
        self.recalled_keys: set[str] = set()
        self.thread_cache: dict[str, dict[str, Any]] = {}
        self.contact_cache: dict[str, dict[str, Any]] = {}
        self.group_member_cache: dict[str, dict[str, str]] = {}
        self.last_auth_check = 0.0
        self.last_auth_status: str | None = None
        self.last_login_page_warn_at = 0.0
        self.suppress_inbound_during_relogin = False
        self.self_id = "agent_wechat"
        self.self_aliases: set[str] = set()
        self._add_self_alias(self.self_id)
        self._add_self_alias(self.config.get("id"))
        self.ws_task: asyncio.Task[None] | None = None
        self.ws_connected = False
        self.last_full_sync_ms = 0.0
        self.active_chat_ids: list[str] = []
        self.chat_locks: dict[str, asyncio.Lock] = {}

        if self not in self.__class__._active_instances:
            self.__class__._active_instances.append(self)

    def meta(self) -> PlatformMetadata:
        return self.metadata

    def get_client(self) -> WeChatClient:
        return self.client

    @staticmethod
    def _normalize_alias(value: str | None) -> str:
        if not value:
            return ""
        alias = str(value).strip()
        if alias.startswith("wx-"):
            alias = alias[3:]
        return alias.strip()

    def _add_self_alias(self, value: str | None) -> None:
        if not value:
            return
        alias = str(value).strip()
        if not alias:
            return

        candidates = {alias}
        normalized = self._normalize_alias(alias)
        if normalized:
            candidates.add(normalized)
        match = SELF_ID_ALIAS_RE.match(alias)
        if match:
            candidates.add(match.group(1))

        for item in candidates:
            if item:
                self.self_aliases.add(item)

    def _remember_seen_message(self, message_id: str) -> None:
        if not message_id:
            return
        if message_id not in self.seen_message_ids:
            self.seen_message_ids.add(message_id)
            self._seen_message_order.append(message_id)
            if len(self._seen_message_order) > 4000:
                oldest = self._seen_message_order[:1000]
                del self._seen_message_order[:1000]
                for item in oldest:
                    self.seen_message_ids.discard(item)
        cls = self.__class__
        if message_id not in cls._global_seen_message_ids:
            cls._global_seen_message_ids.add(message_id)
            cls._global_seen_order.append(message_id)
            if len(cls._global_seen_order) > 8000:
                oldest_global = cls._global_seen_order[:2000]
                del cls._global_seen_order[:2000]
                for item in oldest_global:
                    cls._global_seen_message_ids.discard(item)

    def _is_message_seen(self, message_id: str) -> bool:
        if not message_id:
            return False
        return (
            message_id in self.seen_message_ids
            or message_id in self.in_flight_message_ids
            or message_id in self.__class__._global_seen_message_ids
        )

    def _forget_seen_message(self, message_id: str) -> None:
        if not message_id:
            return
        self.seen_message_ids.discard(message_id)
        with suppress(ValueError):
            self._seen_message_order.remove(message_id)
        cls = self.__class__
        cls._global_seen_message_ids.discard(message_id)
        with suppress(ValueError):
            cls._global_seen_order.remove(message_id)

    def _state_file_path(self) -> str:
        acct = self.client.account_id or "default"
        srv = self.server_id or "default"
        safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{srv}_{acct}")
        return os.path.join(_safe_state_dir(), f"cursor_{safe_key}.json")

    def _load_persisted_state(self) -> None:
        path = self._state_file_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            cursor = str(data.get("cursor") or "").strip()
            if cursor.isdigit():
                self.event_cursor = cursor
            seen = data.get("seenMessageIds")
            if isinstance(seen, list):
                for mid in seen[-2000:]:
                    if isinstance(mid, str) and mid:
                        self._remember_seen_message(mid)
        except Exception as exc:
            logger.warning(f"[agent_wechat] load persisted cursor failed: {exc}")

    def _save_persisted_state(self) -> None:
        path = self._state_file_path()
        try:
            payload = {
                "serverId": self.server_id,
                "accountId": self.client.account_id,
                "cursor": self.event_cursor,
                "seenMessageIds": self._seen_message_order[-1000:],
                "updatedAt": int(time.time()),
            }
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception as exc:
            logger.debug(f"[agent_wechat] save persisted cursor failed: {exc}")

    def _get_chat_lock(self, chat_id: str) -> asyncio.Lock:
        lock = self.chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self.chat_locks[chat_id] = lock
        return lock

    def _touch_chat(self, chat_id: str) -> None:
        if not chat_id:
            return
        if chat_id in self.active_chat_ids:
            self.active_chat_ids.remove(chat_id)
        self.active_chat_ids.insert(0, chat_id)
        if len(self.active_chat_ids) > 16:
            del self.active_chat_ids[16:]

    def _warn_login_page_throttled(self) -> None:
        now = time.monotonic()
        interval_seconds = self.__class__._logout_warn_interval_seconds
        if now - self.last_login_page_warn_at < interval_seconds:
            return

        max_count = self.__class__._logout_warn_max_count
        if max_count > 0 and self.__class__._logout_warn_sent_count >= max_count:
            if not self.__class__._logout_warn_limit_logged:
                logger.warning(
                    "[agent_wechat] 退出登录提醒已达到最大发送数量，"
                    f"max_count={max_count}，等待重新登录后重置"
                )
                self.__class__._logout_warn_limit_logged = True
            return

        self.last_login_page_warn_at = now

        warn_text = (
            "微信账号尚未登录或已离线（authenticated=false / disconnected），"
            "请在聊天中发送 /wxauth 获取登录二维码，或在 agent-wechat 服务器执行 wx auth login"
        )
        logger.warning(f"[agent_wechat] {warn_text}")

        notifier = self.__class__._logout_notifier
        if notifier is None:
            return

        try:
            maybe_result = notifier(warn_text)
            if asyncio.iscoroutine(maybe_result):
                asyncio.create_task(maybe_result)
            self.__class__._logout_warn_sent_count += 1
        except Exception as exc:
            logger.warning(f"[agent_wechat] 退出登录提醒回调失败: {exc}")

    async def terminate(self) -> None:
        self.shutdown_event.set()
        if self in self.__class__._active_instances:
            self.__class__._active_instances.remove(self)
        if self.ws_task is not None:
            self.ws_task.cancel()

    async def send_by_session(
        self,
        session: MessageSesion,
        message_chain,
    ) -> None:
        await AgentWeChatMessageEvent.send_message_chain(
            self.client,
            session.session_id,
            message_chain,
        )
        self._touch_chat(str(session.session_id))
        await super().send_by_session(session, message_chain)

    async def run(self) -> None:
        # 防止插件热重载或配置保存时旧适配器实例残留在后台并存运行
        for prev in list(self.__class__._active_instances):
            if prev is not self and getattr(getattr(prev, "metadata", None), "id", None) == self.metadata.id:
                if hasattr(prev, "shutdown_event"):
                    prev.shutdown_event.set()
                prev_ws = getattr(prev, "ws_task", None)
                if prev_ws is not None:
                    prev_ws.cancel()

        logger.info(
            f"[agent_wechat] adapter started id={self.metadata.id} version=1.0.1"
        )
        await self._refresh_auth_if_needed()
        self.ws_task = asyncio.create_task(self._run_events_stream())
        try:
            while not self.shutdown_event.is_set():
                event_triggered = False
                try:
                    await asyncio.wait_for(
                        self.sync_event.wait(),
                        timeout=max(0.2, POLL_INTERVAL_MS / 1000),
                    )
                    event_triggered = True
                except asyncio.TimeoutError:
                    pass

                self.sync_event.clear()
                if self.shutdown_event.is_set():
                    break

                try:
                    if not await self._refresh_auth_if_needed():
                        continue

                    now_ms = time.time() * 1000
                    should_poll = event_triggered or (
                        not self.ws_connected
                        and (now_ms - self.last_full_sync_ms) >= EVENT_SYNC_INTERVAL_MS
                    )
                    if should_poll:
                        await self._sync_events_once()
                        self.last_full_sync_ms = now_ms
                except Exception as exc:
                    logger.exception(f"[agent_wechat] sync failed: {exc}")
        finally:
            self.shutdown_event.set()
            if self in self.__class__._active_instances:
                self.__class__._active_instances.remove(self)
            if self.ws_task is not None:
                self.ws_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.ws_task

    async def _run_events_stream(self) -> None:
        stream_client = WeChatEventStreamClient(
            lambda: self.client.build_events_stream_url(after=self.event_cursor),
            token=self.client.token,
            verify_tls=self.client.verify_tls,
            on_open=self._on_ws_open,
            on_message=self._on_ws_message,
            on_close=self._on_ws_close,
            on_error=self._on_ws_error,
        )
        await stream_client.run_forever(self.shutdown_event)

    async def _on_ws_open(self) -> None:
        self.ws_connected = True

    async def _on_ws_close(self) -> None:
        self.ws_connected = False
        if not self.shutdown_event.is_set():
            logger.debug("[agent_wechat] events SSE stream closed, will reconnect")

    async def _on_ws_error(self, exc: Exception) -> None:
        if not self.shutdown_event.is_set():
            err_text = str(exc)
            if "HTTP 401" in err_text or "unauthorized" in err_text.lower():
                logger.warning(
                    "[agent_wechat] events stream 鉴权失败 (HTTP 401)："
                    "请检查平台配置的 Bearer Token（在 agent-wechat 服务器查看 "
                    "~/.local/share/agent-wechat/api-token 或运行 wx tokens create）"
                )
                return
            logger.warning(f"[agent_wechat] events stream error: {exc}")

    async def _on_ws_message(self, raw_message: str) -> None:
        if not raw_message:
            return

        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        async with self.event_lock:
            await self._dispatch_ws_payload(payload, source="v1-sse")

    async def _dispatch_ws_payload(
        self, payload: Any, *, source: str = "v1-event"
    ) -> None:
        if self.suppress_inbound_during_relogin:
            return

        if isinstance(payload, list):
            for item in payload:
                await self._dispatch_ws_payload(item, source=source)
            return

        if not isinstance(payload, dict):
            return

        if payload.get("sseEvent") == "error" or isinstance(payload.get("error"), dict):
            err_code = str((payload.get("error") or {}).get("code") or "")
            if err_code == "event_cursor_expired":
                logger.warning(
                    "[agent_wechat] event cursor expired on SSE stream, rebaselining cursor"
                )
                await self._rebaseline_event_cursor()
            return

        event_kind = str(
            payload.get("kind") or payload.get("type") or payload.get("event") or ""
        )
        cursor = str(payload.get("cursor") or "").strip()

        if event_kind in {"ping", "pong"}:
            return

        if event_kind in {
            "account.updated",
            "operation.updated",
            "auth",
            "login",
            "login_state",
            "status",
        }:
            self.last_auth_check = 0
            if cursor.isdigit():
                self._advance_cursor(cursor)
            return

        if event_kind in {"message.committed", "message.recalled"}:
            await self._handle_native_event(payload, source=source)
            return

        # 兼容直接携带 message 字典的自定义测试事件
        if event_kind in {
            "message",
            "message_received",
            "message_created",
            "wechat_message",
        }:
            message = payload.get("message")
            chat = payload.get("chat") or {}
            if isinstance(message, dict):
                converted = await self._convert_native_message(message, chat_hint=chat)
                if converted is not None:
                    await self.handle_msg(converted)
            return

        if cursor.isdigit():
            self._advance_cursor(cursor)

    def _advance_cursor(self, new_cursor: str) -> None:
        if not new_cursor or not new_cursor.isdigit():
            return
        if not self.event_cursor.isdigit() or int(new_cursor) > int(self.event_cursor):
            self.event_cursor = new_cursor
            self._save_persisted_state()

    async def _ack_current_cursor(self) -> None:
        if not self.event_cursor.isdigit() or int(self.event_cursor) <= 0:
            return
        with suppress(Exception):
            await asyncio.to_thread(
                self.client.ack_events,
                self.consumer_id,
                self.event_cursor,
            )

    async def _handle_native_event(
        self, event: dict[str, Any], *, source: str = "v1-event"
    ) -> None:
        kind = str(event.get("kind") or "")
        cursor = str(event.get("cursor") or "").strip()
        data = event.get("data") if isinstance(event.get("data"), dict) else {}

        if kind == "message.recalled":
            thread_id = str(data.get("threadId") or "")
            server_id = str(data.get("serverId") or "")
            message_id = str(data.get("messageId") or "")
            if thread_id and server_id:
                self.recalled_keys.add(f"{thread_id}:{server_id}")
            if message_id:
                self.recalled_keys.add(message_id)
            if cursor.isdigit():
                self._advance_cursor(cursor)
                await self._ack_current_cursor()
            return

        if kind == "message.committed":
            message_id = str(data.get("messageId") or "").strip()
            origin = str(data.get("origin") or "live").strip()
            direction = str(data.get("direction") or "incoming").strip()

            # 过滤非实时消息（如历史导入 profile_import、首次同步 catchup、或自身发出的 local_send）
            if origin in {"profile_import", "catchup", "local_send"} or direction == "outgoing":
                if message_id:
                    self._remember_seen_message(message_id)
                if cursor.isdigit():
                    self._advance_cursor(cursor)
                    await self._ack_current_cursor()
                return

            if not message_id or self._is_message_seen(message_id):
                if cursor.isdigit():
                    self._advance_cursor(cursor)
                    await self._ack_current_cursor()
                return

            # 在发起异步 HTTP 拉取前立即登记 message_id，防止 SSE 与轮询协程并发重入导致重复消费
            self.in_flight_message_ids.add(message_id)
            self._remember_seen_message(message_id)
            try:
                try:
                    message = await asyncio.to_thread(
                        self.client.get_message,
                        message_id,
                        timeout=HOT_PATH_TIMEOUT_SECONDS,
                    )
                except AgentWeChatAPIError as exc:
                    if exc.status_code in {404, 410}:
                        if cursor.isdigit():
                            self._advance_cursor(cursor)
                            await self._ack_current_cursor()
                        return
                    self._forget_seen_message(message_id)
                    raise
                except Exception:
                    self._forget_seen_message(message_id)
                    raise

                if isinstance(message, dict) and message:
                    thread_id = str(message.get("threadId") or "").strip()
                    server_id = str(message.get("serverId") or "").strip()
                    server_dedup_key = (
                        f"srv:{thread_id}:{server_id}"
                        if thread_id and server_id
                        else ""
                    )
                    if server_dedup_key and self._is_message_seen(server_dedup_key):
                        if cursor.isdigit():
                            self._advance_cursor(cursor)
                            await self._ack_current_cursor()
                        return
                    if server_dedup_key:
                        self._remember_seen_message(server_dedup_key)

                    converted = await self._convert_native_message(message)
                    if converted is not None:
                        self._log_inbound_message(
                            source=source,
                            message=message,
                            session_id=converted.session_id,
                        )
                        await self.handle_msg(converted)
                        if thread_id:
                            self._touch_chat(thread_id)
                            await self._maybe_mark_thread_read(thread_id)

                if cursor.isdigit():
                    self._advance_cursor(cursor)
                    await self._ack_current_cursor()
            finally:
                self.in_flight_message_ids.discard(message_id)

    async def _sync_events_once(self) -> None:
        """通过 GET /v1/accounts/{a}/events?after=CURSOR 做增量补偿同步。"""
        async with self.event_lock:
            for _ in range(10):
                if self.shutdown_event.is_set():
                    return
                try:
                    page = await asyncio.to_thread(
                        self.client.list_events,
                        self.event_cursor,
                        100,
                        timeout=HOT_PATH_TIMEOUT_SECONDS,
                    )
                except AgentWeChatAPIError as exc:
                    if exc.code == "event_cursor_expired" or exc.status_code == 410:
                        logger.warning(
                            "[agent_wechat] event cursor expired during poll, rebaselining"
                        )
                        await self._rebaseline_event_cursor()
                        return
                    raise

                items = page.get("items") if isinstance(page, dict) else []
                if not isinstance(items, list) or not items:
                    return

                before = self.event_cursor
                for item in items:
                    if self.shutdown_event.is_set():
                        return
                    if isinstance(item, dict):
                        await self._dispatch_ws_payload(item, source="v1-poll")

                next_cursor = str(page.get("nextCursor") or "").strip()
                if next_cursor.isdigit():
                    self._advance_cursor(next_cursor)
                    await self._ack_current_cursor()

                if len(items) < 100 or self.event_cursor == before:
                    return

    async def _refresh_auth_if_needed(self) -> bool:
        now_ms = time.time() * 1000
        if (
            now_ms - self.last_auth_check < AUTH_POLL_INTERVAL_MS
            and self.last_auth_status == "logged_in"
        ):
            return True

        self.last_auth_check = now_ms
        prev_status = self.last_auth_status
        try:
            if self.server_id is None:
                with suppress(Exception):
                    daemon_status = await asyncio.to_thread(self.client.status)
                    if isinstance(daemon_status, dict) and daemon_status.get("serverId"):
                        self.server_id = str(daemon_status["serverId"])

            auth = await asyncio.to_thread(self.client.auth_status)
        except Exception as exc:
            logger.warning(f"[agent_wechat] auth check failed: {exc}")
            self.last_auth_status = None
            return False

        self.last_auth_status = str(auth.get("status") or "unknown")
        if auth.get("loggedInUser"):
            self.self_id = str(auth["loggedInUser"])
            self._add_self_alias(self.self_id)
        if auth.get("alias"):
            self._add_self_alias(str(auth["alias"]))

        if self.last_auth_status != "logged_in":
            # 若已认证但处于 disconnected，尝试自动唤醒连接
            if bool(auth.get("authenticated")) and auth.get("connection") == "disconnected":
                with suppress(Exception):
                    await asyncio.to_thread(self.client.connect)
            if self.last_auth_status == "logged_out":
                self._warn_login_page_throttled()
            return False

        if prev_status != "logged_in":
            self.__class__.reset_logout_warning_state()
            self._load_persisted_state()
            if self.event_cursor == "0":
                await self._rebaseline_event_cursor()

        self.last_login_page_warn_at = 0.0
        return True

    async def _rebaseline_event_cursor(self) -> None:
        """首次启动或游标过期时，将事件游标对齐到当前最新边界，避免回灌历史消息。"""
        self.suppress_inbound_during_relogin = True
        try:
            threads_page = await asyncio.to_thread(
                self.client.list_threads,
                50,
                None,
                timeout=HOT_PATH_TIMEOUT_SECONDS,
            )
            if isinstance(threads_page, dict):
                for item in threads_page.get("items") or []:
                    if isinstance(item, dict) and item.get("id") and not item.get("deleted"):
                        self.thread_cache[str(item["id"])] = item

                boundary = str(threads_page.get("asOfEventCursor") or "").strip()
                if boundary.isdigit() and int(boundary) > 0:
                    self._advance_cursor(boundary)
                    await self._ack_current_cursor()
                    return

            # 若 threads 未返回 asOfEventCursor，则顺推事件日志至末尾
            cursor = self.event_cursor if self.event_cursor.isdigit() else "0"
            for _ in range(50):
                page = await asyncio.to_thread(
                    self.client.list_events,
                    cursor,
                    200,
                    timeout=HOT_PATH_TIMEOUT_SECONDS,
                )
                items = page.get("items") if isinstance(page, dict) else []
                next_cursor = str((page or {}).get("nextCursor") or cursor).strip()
                if next_cursor.isdigit():
                    cursor = next_cursor
                if not isinstance(items, list) or len(items) < 200:
                    break

            if cursor.isdigit():
                self._advance_cursor(cursor)
                await self._ack_current_cursor()
        except Exception as exc:
            logger.warning(f"[agent_wechat] baseline event cursor sync failed: {exc}")
        finally:
            self.suppress_inbound_during_relogin = False

    async def _maybe_mark_thread_read(self, thread_id: str) -> None:
        read_policy = str(self.config.get("read_policy") or "on_inbound").lower()
        if read_policy == "never" or not thread_id:
            return
        try:
            thread = await asyncio.to_thread(
                self.client.get_thread,
                thread_id,
                timeout=HOT_PATH_TIMEOUT_SECONDS,
            )
            if not isinstance(thread, dict):
                return
            self.thread_cache[thread_id] = thread
            expected_version = str(thread.get("readStateVersion") or "").strip()
            unread = int(thread.get("unreadCount") or 0)
            if expected_version and unread > 0:
                await asyncio.to_thread(
                    self.client.mark_thread_read,
                    thread_id,
                    expected_version,
                    timeout=HOT_PATH_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            logger.debug(
                f"[agent_wechat] mark_thread_read skipped for {thread_id}: {exc}"
            )

    async def _resolve_thread_info(self, thread_id: str) -> dict[str, Any]:
        cached = self.thread_cache.get(thread_id)
        if isinstance(cached, dict):
            return cached
        try:
            thread = await asyncio.to_thread(
                self.client.get_thread,
                thread_id,
                timeout=HOT_PATH_TIMEOUT_SECONDS,
            )
            if isinstance(thread, dict):
                self.thread_cache[thread_id] = thread
                return thread
        except Exception:
            pass
        return {"id": thread_id, "peerId": thread_id}

    async def _resolve_sender_display_name(
        self,
        thread_id: str,
        sender_id: str,
        is_group: bool,
    ) -> str:
        if not sender_id:
            return "WeChat"

        if is_group:
            room_members = self.group_member_cache.get(thread_id)
            if room_members is None:
                room_members = {}
                with suppress(Exception):
                    page = await asyncio.to_thread(
                        self.client.list_group_members, thread_id, 200
                    )
                    for item in (page or {}).get("items") or []:
                        if isinstance(item, dict) and not item.get("deleted"):
                            uid = str(item.get("username") or item.get("id") or "")
                            label = str(
                                item.get("roomDisplayName")
                                or item.get("displayName")
                                or item.get("mentionName")
                                or ""
                            ).strip()
                            if uid and label:
                                room_members[uid] = label
                self.group_member_cache[thread_id] = room_members
            if sender_id in room_members:
                return room_members[sender_id]

        contact = self.contact_cache.get(sender_id)
        if contact is None:
            with suppress(Exception):
                fetched = await asyncio.to_thread(self.client.get_contact, sender_id)
                if isinstance(fetched, dict):
                    contact = fetched
                    self.contact_cache[sender_id] = fetched
        if isinstance(contact, dict):
            name = (
                contact.get("displayName")
                or contact.get("remark")
                or contact.get("nickname")
            )
            if name:
                return str(name)

        return sender_id

    async def _convert_native_message(
        self,
        message: dict[str, Any],
        *,
        chat_hint: dict[str, Any] | None = None,
    ) -> AstrBotMessage | None:
        """将 agent-wechat v1 NativeMessage 转换为 AstrBotMessage。"""
        # 兼容旧版格式测试输入
        if "localId" in message and "threadId" not in message:
            return await self._convert_message(chat_hint or {}, message)

        if message.get("deleted") is True or message.get("recall") is not None:
            return None

        direction = str(message.get("direction") or "incoming")
        if direction == "outgoing" or bool(message.get("isSelf")):
            self._add_self_alias(message.get("senderId"))
            return None

        thread_id = str(
            message.get("threadId")
            or (chat_hint or {}).get("id")
            or (chat_hint or {}).get("username")
            or ""
        ).strip()
        if not thread_id:
            return None

        server_id = str(message.get("serverId") or "").strip()
        message_id = str(message.get("id") or "").strip()
        if (
            (thread_id and server_id and f"{thread_id}:{server_id}" in self.recalled_keys)
            or (message_id and message_id in self.recalled_keys)
        ):
            return None

        thread_info = chat_hint or await self._resolve_thread_info(thread_id)
        classification = (
            thread_info.get("classification")
            if isinstance(thread_info.get("classification"), dict)
            else None
        )
        if is_official_account(thread_id, classification):
            return None

        msg_type = int(message.get("type", 0) or 0) & 0x7FFFFFFF
        app_msg_type = message.get("appMessageType")
        if msg_type not in SUPPORTED_MSG_TYPES:
            return None
        # 文件上传预告 (49/74) 不触发机器人轮次
        if msg_type == MSG_TYPE_APP and app_msg_type == 74:
            return None

        is_group = is_group_chat(thread_id, str(thread_info.get("kind") or ""))
        sender_id = str(message.get("senderId") or message.get("sender") or "").strip()
        if not sender_id:
            if is_group:
                # 群系统提示无发送者，不作为用户消息触发
                return None
            sender_id = thread_id

        raw_text = str(message.get("body") or message.get("content") or "")
        mentions_obj = (
            message.get("mentions")
            if isinstance(message.get("mentions"), dict)
            else None
        )
        was_mentioned = is_native_mentioned(mentions_obj, raw_text, self.self_aliases)

        # 微信的「引用」不会带 at，但引用机器人自己的消息就是在对机器人说话。
        # 只放行这一种引用，不放开成「任何引用都转发」，否则又回到群消息全触发的状态。
        quoted_self = False
        quote_obj = message.get("quote")
        if (
            is_group
            and isinstance(quote_obj, dict)
            and quote_obj.get("state") != "recalled"
        ):
            quoted_from = str(quote_obj.get("senderId") or "").strip()
            quoted_self = bool(quoted_from) and (
                quoted_from == self.self_id
                or quoted_from in self.self_aliases
                or self._normalize_alias(quoted_from) in self.self_aliases
            )

        require_mention = bool(self.config.get("require_group_mention", False))
        dm_policy = str(self.config.get("dm_policy") or "open")
        dm_allowlist = normalize_allowlist(self.config.get("dm_allowlist"))
        group_policy = str(self.config.get("group_policy") or "open")
        group_allowlist = normalize_allowlist(self.config.get("group_allowlist"))

        # 群聊 allowlist 既支持填群 ID (xxx@chatroom)，也支持填群成员 wxid
        effective_group_sender = (
            thread_id
            if is_group and thread_id in group_allowlist
            else sender_id
        )
        allowed, _ = should_forward_message(
            is_group=is_group,
            sender_id=effective_group_sender,
            was_mentioned=was_mentioned or quoted_self,
            require_mention=require_mention,
            dm_policy=dm_policy,
            dm_allowlist=dm_allowlist,
            group_policy=group_policy,
            group_allowlist=group_allowlist,
        )
        if not allowed:
            return None

        normalized_text = (
            strip_leading_mentions(raw_text) if is_group else raw_text.strip()
        )

        components: list[Any] = []
        message_str_parts: list[str] = []

        # 引用回复 (49/57 或携带 quote 对象)
        quote = message.get("quote")
        if isinstance(quote, dict) and quote.get("state") != "recalled":
            q_sender = str(quote.get("senderId") or "unknown")
            q_text = str(quote.get("embeddedText") or "").strip()
            q_srv_id = str(quote.get("serverId") or "")
            if q_text:
                components.append(
                    Reply(
                        id=q_srv_id or message_id,
                        chain=[Plain(text=q_text)],
                        sender_id=q_sender,
                        sender_nickname=q_sender,
                        message_str=q_text,
                        text=q_text,
                    )
                )
                message_str_parts.append(f"[引用 {q_sender}: {q_text[:120]}]")

        # 只有真的被 @ 才注入 At。require_mention=False 的语义是「把群消息放行到
        # 框架」（让群聊上下文与主动接话能看到消息），不是「把每条消息都当成在叫机器人」。
        # 否则 AstrBot 的唤醒判定会把每条群消息都当成被 @，于是每条都回。
        if is_group and was_mentioned:
            components.append(At(qq=self.self_id, name="bot"))

        # 位置消息 (48)
        location = message.get("location")
        if msg_type == MSG_TYPE_LOCATION and isinstance(location, dict):
            lat = float(location.get("latitude") or 0.0)
            lon = float(location.get("longitude") or 0.0)
            loc_name = str(location.get("name") or "")
            loc_addr = str(location.get("address") or raw_text or "")
            components.append(
                Location(lat=lat, lon=lon, title=loc_name, content=loc_addr)
            )
            loc_summary = f"[位置] {loc_name} {loc_addr} ({lat}, {lon})".strip()
            message_str_parts.append(loc_summary)
        elif msg_type == MSG_TYPE_APP and isinstance(message.get("link"), dict):
            # 链接卡片 (49/3,4,5,51)
            link = message["link"]
            l_title = str(link.get("title") or "链接分享")
            l_desc = str(link.get("description") or "")
            l_url = str(link.get("url") or "")
            if l_url:
                components.append(Share(url=l_url, title=l_title, content=l_desc))
            link_summary = "\n".join(
                part for part in [f"[链接] {l_title}", l_desc, l_url] if part
            )
            components.append(Plain(text=link_summary))
            message_str_parts.append(link_summary)
        elif normalized_text:
            components.append(Plain(text=normalized_text))
            message_str_parts.append(normalized_text)

        # 下载媒体附件（图片 3、语音 34、视频 43、表情 47、文件 49/6、或表情分享 49/8）
        should_download_media = msg_type in {
            MSG_TYPE_IMAGE,
            MSG_TYPE_VOICE,
            MSG_TYPE_VIDEO,
            MSG_TYPE_STICKER,
        } or (msg_type == MSG_TYPE_APP and app_msg_type in {None, 6, 8})
        if should_download_media and message_id:
            media = await self._download_v1_media(
                thread_id=thread_id,
                message_id=message_id,
                msg_type=msg_type,
            )
            if media is not None:
                path, mime_type, filename = media
                components.append(_mime_to_component(path, mime_type, filename))
                if not normalized_text:
                    if mime_type.startswith("image/"):
                        message_str_parts.append("<media:image>")
                    elif mime_type.startswith("audio/"):
                        message_str_parts.append("<media:audio>")
                    elif mime_type.startswith("video/"):
                        message_str_parts.append("<media:video>")
                    else:
                        message_str_parts.append(f"[file:{filename}]")

        if not components:
            return None

        sender_name = await self._resolve_sender_display_name(
            thread_id, sender_id, is_group
        )
        abm = AstrBotMessage()
        abm.self_id = self.self_id
        abm.sender = MessageMember(user_id=sender_id, nickname=sender_name)
        abm.message = components
        abm.message_str = "\n".join(part for part in message_str_parts if part).strip()
        abm.raw_message = {
            "thread": thread_info,
            "message": message,
        }
        abm.timestamp = _parse_timestamp(
            cast(str | None, message.get("createdAt") or message.get("timestamp"))
        )
        # 若有 serverId，编码为 "{messageId}|{serverId}"，方便下游插件使用 Reply 组件原样发起引用回复
        abm.message_id = (
            f"{message_id}|{server_id}"
            if message_id and server_id
            else (message_id or f"wechat:{thread_id}:{int(time.time())}")
        )

        if is_group:
            abm.type = MessageType.GROUP_MESSAGE
            abm.group_id = thread_id
            abm.group = Group(thread_id)
            abm.group.group_name = str(
                thread_info.get("displayName")
                or thread_info.get("name")
                or thread_id
            )
            abm.session_id = thread_id
        else:
            abm.type = MessageType.FRIEND_MESSAGE
            abm.session_id = thread_id if thread_id == "filehelper" else sender_id

        return abm

    async def _download_v1_media(
        self,
        *,
        thread_id: str,
        message_id: str,
        msg_type: int,
    ) -> tuple[str, str, str] | None:
        try:
            downloaded = await asyncio.to_thread(
                self.client.download_message_media,
                message_id,
                msg_type=msg_type,
                quality="best",
                wait_seconds=MEDIA_WAIT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                f"[agent_wechat] v1 media download failed for {thread_id} ({message_id}): {exc}"
            )
            return None

        if downloaded is None:
            return None

        raw_bytes, mime_type, filename = downloaded
        directory = os.path.join(_safe_temp_dir(), "agent_wechat_bridge")
        os.makedirs(directory, exist_ok=True)
        safe_chat = re.sub(r"[^A-Za-z0-9_.-]+", "_", thread_id)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename) or "attachment.bin"
        path = os.path.join(
            directory, f"{safe_chat}_{int(time.time() * 1000)}_{safe_name}"
        )
        with open(path, "wb") as handle:
            handle.write(raw_bytes)
        return path, mime_type, filename

    # ------------------------------------------------------------------
    # Legacy conversion helper kept for unit-test backward compatibility
    # ------------------------------------------------------------------

    async def _convert_message(
        self,
        chat: dict[str, Any],
        message: dict[str, Any],
    ) -> AstrBotMessage | None:
        chat_id = str(chat.get("username") or chat.get("id") or "")
        sender_id = str(message.get("sender") or message.get("senderId") or chat_id)
        sender_name = str(
            message.get("senderName")
            or sender_id
            or chat.get("displayName")
            or chat.get("name")
            or "WeChat"
        )
        is_group = is_group_chat(chat_id) or bool(chat.get("isGroup"))
        raw_text = str(message.get("content") or message.get("body") or "")
        is_mentioned = bool(message.get("isMentioned"))
        if is_group and not is_mentioned:
            is_mentioned = is_native_mentioned(
                message.get("mentions")
                if isinstance(message.get("mentions"), dict)
                else None,
                raw_text,
                self.self_aliases,
            )
        if is_group and not bool(self.config.get("require_group_mention", False)):
            is_mentioned = True
        normalized_text = (
            strip_leading_mentions(raw_text) if is_group else raw_text.strip()
        )

        components: list[Any] = []
        message_str_parts: list[str] = []

        if is_group and is_mentioned:
            components.append(At(qq=self.self_id, name="bot"))

        if normalized_text:
            components.append(Plain(text=normalized_text))
            message_str_parts.append(normalized_text)

        reply = message.get("reply")
        if isinstance(reply, dict) and reply.get("content"):
            quoted = str(reply.get("content"))[:80]
            reply_block = f"[reply to {reply.get('sender') or 'unknown'}] {quoted}"
            components.append(Plain(text=reply_block))
            message_str_parts.append(reply_block)

        if not components:
            return None

        abm = AstrBotMessage()
        abm.self_id = self.self_id
        abm.sender = MessageMember(user_id=sender_id, nickname=sender_name)
        abm.message = components
        abm.message_str = "\n".join(part for part in message_str_parts if part).strip()
        abm.raw_message = {
            "chat": chat,
            "message": message,
        }
        abm.timestamp = _parse_timestamp(cast(str | None, message.get("timestamp")))
        abm.message_id = f"wechat:{chat_id}:{message.get('localId') or message.get('id')}"

        if is_group:
            abm.type = MessageType.GROUP_MESSAGE
            abm.group_id = chat_id
            abm.group = Group(chat_id)
            abm.group.group_name = str(
                chat.get("displayName") or chat.get("name") or chat_id
            )
            abm.session_id = chat_id
        else:
            abm.type = MessageType.FRIEND_MESSAGE
            abm.session_id = sender_id

        return abm

    def _log_inbound_message(
        self,
        *,
        source: str,
        message: dict[str, Any],
        session_id: str,
        chat: dict[str, Any] | None = None,
    ) -> None:
        chat_id = str(
            message.get("threadId")
            or (chat or {}).get("username")
            or (chat or {}).get("id")
            or ""
        )
        sender_id = str(
            message.get("senderId") or message.get("sender") or chat_id or ""
        )
        msg_id = str(message.get("id") or message.get("localId") or "")
        raw_type = int(message.get("type", 0) or 0)
        base_type = raw_type & 0x7FFFFFFF
        mentions = message.get("mentions")
        mentioned = (
            mentions.get("self")
            if isinstance(mentions, dict)
            else message.get("isMentioned")
        )
        logger.info(
            "[agent_wechat] inbound accepted "
            f"source={source} "
            f"chat={chat_id} "
            f"sender={sender_id} "
            f"id={msg_id} "
            f"type={base_type} "
            f"mentioned={mentioned} "
            f"session={session_id}"
        )

    async def handle_msg(self, message: AstrBotMessage) -> None:
        is_group = getattr(message, "type", None) == MessageType.GROUP_MESSAGE
        chat_id = (
            getattr(message, "group_id", None) if is_group else message.session_id
        )
        event = AgentWeChatMessageEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            client=self.client,
            chat_id=str(chat_id),
            is_group=bool(is_group),
        )
        self.commit_event(event)
