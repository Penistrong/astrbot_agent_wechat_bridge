"""插件根入口。

框架要求插件根目录存在 `main.py`。该文件用于将根入口桥接到
`src/` 下的实际适配器实现，并提供远程扫码登录指令 `/wxauth` 与状态查询指令 `/wxstatus`。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

try:
    from .src.agent_wechat_client import WeChatClient
    from .src.agent_wechat_platform_adapter import (
        AgentWeChatPlatformAdapter as _AgentWeChatPlatformAdapter,
    )
except ImportError:
    from src.agent_wechat_client import WeChatClient
    from src.agent_wechat_platform_adapter import (
        AgentWeChatPlatformAdapter as _AgentWeChatPlatformAdapter,
    )

AgentWeChatPlatformAdapter = _AgentWeChatPlatformAdapter


@register(
    "astrbot_agent_wechat_bridge",
    "Codex",
    "AstrBot platform adapter for agent-wechat (1.x Native /v1 API).",
    "1.0.0",
)
class AgentWeChatBridgePlugin(Star):
    """加载平台适配器并提供远程登录与状态管理命令。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or AstrBotConfig()
        AgentWeChatPlatformAdapter.set_logout_notifier(self._notify_logout)
        AgentWeChatPlatformAdapter.set_logout_notify_policy(
            interval_seconds=self._get_logout_notify_interval_seconds(),
            max_count=self._get_logout_notify_max_count(),
        )

    def _get_logout_notify_umos(self) -> list[str]:
        raw = self.config.get("logout_notify_umos", [])
        if not isinstance(raw, list):
            return []
        umos: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if value:
                umos.append(value)
        return umos

    def _get_logout_notify_max_count(self) -> int:
        raw = self.config.get("logout_notify_max_count", 0)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 0
        return max(0, value)

    def _get_logout_notify_interval_seconds(self) -> float:
        raw = self.config.get("logout_notify_interval_seconds", 60)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 60.0
        return max(1.0, value)

    def _resolve_active_client(self) -> WeChatClient | None:
        adapter = AgentWeChatPlatformAdapter.get_active_instance()
        if adapter is not None:
            return adapter.get_client()
        return None

    async def _broadcast_chain(self, chain: MessageChain) -> int:
        umos = self._get_logout_notify_umos()
        if not umos:
            return 0

        sent = 0
        for umo in umos:
            try:
                ok = await self.context.send_message(umo, chain)
                if ok:
                    sent += 1
                else:
                    logger.warning(f"[agent_wechat] UMO 发送失败: {umo}")
            except Exception as exc:
                logger.warning(f"[agent_wechat] UMO 发送异常: umo={umo} err={exc}")
        return sent

    async def _notify_logout(self, warn_text: str) -> None:
        _ = warn_text
        AgentWeChatPlatformAdapter.set_logout_notify_policy(
            interval_seconds=self._get_logout_notify_interval_seconds(),
            max_count=self._get_logout_notify_max_count(),
        )
        chain = MessageChain().message(
            "[agent-wechat] 微信账号当前未登录或已断开连接，请发送 /wxauth 获取远程登录二维码。"
        )
        await self._broadcast_chain(chain)

    async def terminate(self) -> None:
        AgentWeChatPlatformAdapter.set_logout_notifier(None)
        AgentWeChatPlatformAdapter.reset_logout_warning_state()

    @filter.command("wxstatus")
    async def wxstatus(self, event: AstrMessageEvent):
        """查询远端 agent-wechat 守护进程与微信账号连接状态。"""
        client = self._resolve_active_client()
        if client is None:
            yield event.plain_result(
                "未找到运行中的 agent_wechat 平台实例，请先在 AstrBot 平台管理中启用 agent_wechat。"
            )
            return

        try:
            daemon = await asyncio.to_thread(client.status)
            auth = await asyncio.to_thread(client.auth_status)
        except Exception as exc:
            yield event.plain_result(f"查询远端 agent-wechat 状态失败: {exc}")
            return

        version_info = daemon.get("version") if isinstance(daemon, dict) else {}
        ver_str = (
            version_info.get("version")
            if isinstance(version_info, dict)
            else "unknown"
        )
        summary = (
            f"[agent-wechat 状态]\n"
            f"- 服务地址: {client.base_url}\n"
            f"- 守护进程版本: {ver_str}\n"
            f"- 账号 ID: {auth.get('accountId') or client.account_id}\n"
            f"- 微信用户: {auth.get('loggedInUser') or '未获取'}\n"
            f"- 认证状态: {'已认证' if auth.get('authenticated') else '未认证'}\n"
            f"- 连接状态: {auth.get('connection')}"
        )
        yield event.plain_result(summary)

    @filter.command("wxauth")
    async def wxauth(self, event: AstrMessageEvent):
        """通过远端 agent-wechat /v1 API 发起登录并获取 PNG 二维码。"""
        client = self._resolve_active_client()
        if client is None:
            yield event.plain_result(
                "未找到运行中的 agent_wechat 平台实例，请先在平台管理中启用 agent_wechat。"
            )
            return

        yield event.plain_result(
            "正在向远端 agent-wechat 服务请求微信登录操作，请稍候..."
        )

        try:
            op, qr_png_bytes = await self._start_and_wait_for_qr(client)
        except Exception as exc:
            yield event.plain_result(f"请求远端 agent-wechat 登录失败: {exc}")
            return

        state = str(op.get("state") or "unknown")
        if state == "succeeded":
            yield event.plain_result(
                "远端微信账号已通过已保存的设备信任自动恢复登录，无需扫码！"
            )
            return

        if not qr_png_bytes:
            yield event.plain_result(
                f"登录操作已发起 (operation={op.get('id')}, state={state})，"
                "请在手机微信上确认登录，或稍后重试 `/wxauth`。"
            )
            return

        qr_path = self._save_qr_png(qr_png_bytes)
        yield event.chain_result(
            [
                event.plain_result(
                    "[agent-wechat] 请使用手机微信扫描下方二维码并确认登录："
                ).chain[0],
            ]
        )
        yield event.image_result(qr_path)

        chain = (
            MessageChain()
            .message("[agent-wechat] 微信远程登录二维码，请使用手机微信扫码确认：")
            .file_image(qr_path)
        )
        sent_count = await self._broadcast_chain(chain)
        if sent_count > 0:
            yield event.plain_result(
                f"同时已将登录二维码推送到 {sent_count} 个提醒会话。"
            )

    async def _start_and_wait_for_qr(
        self, client: WeChatClient
    ) -> tuple[dict, bytes | None]:
        op = await asyncio.to_thread(
            client.start_login,
            strategy="auto",
            allow_qr=True,
            force_qr=False,
            timeout_seconds=300,
        )
        op_id = str(op.get("id") or "").strip()
        if not op_id:
            return op, None

        terminal_states = {
            "succeeded",
            "failed",
            "cancelled",
            "expired",
            "rejected",
            "needs_login",
        }
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            state = str(op.get("state") or "")
            if state in terminal_states:
                return op, None

            result = op.get("result") if isinstance(op.get("result"), dict) else {}
            if result.get("qrAvailable") is True:
                generation = result.get("qrGeneration")
                gen_int = int(generation) if isinstance(generation, int) else None
                png_bytes = await asyncio.to_thread(
                    client.get_login_qr_png, op_id, gen_int
                )
                return op, png_bytes

            if state == "awaiting_approval":
                return op, None

            await asyncio.sleep(0.5)
            op = await asyncio.to_thread(client.get_operation, op_id)

        return op, None

    @staticmethod
    def _save_qr_png(png_bytes: bytes) -> str:
        directory = os.path.join(tempfile.gettempdir(), "agent_wechat_bridge")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"wx_login_qr_{int(time.time())}.png")
        with open(path, "wb") as handle:
            handle.write(png_bytes)
        return path
