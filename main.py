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
from astrbot.api.event.filter import EventMessageType
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.message_type import MessageType

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# 清理可能残留在顶层命名空间的旧模块缓存，确保插件热重载/更新后总是加载最新代码
for _mod_name in list(sys.modules.keys()):
    if _mod_name == "src" or _mod_name.startswith("src.agent_wechat"):
        sys.modules.pop(_mod_name, None)

try:
    from .src.agent_wechat_client import WeChatClient
    from .src.agent_wechat_autojoin import AutoJoinGate, looks_like_question
    from .src.agent_wechat_platform_adapter import (
        AgentWeChatPlatformAdapter as _AgentWeChatPlatformAdapter,
    )
except ImportError:
    from src.agent_wechat_client import WeChatClient
    from src.agent_wechat_autojoin import AutoJoinGate, looks_like_question
    from src.agent_wechat_platform_adapter import (
        AgentWeChatPlatformAdapter as _AgentWeChatPlatformAdapter,
    )

AgentWeChatPlatformAdapter = _AgentWeChatPlatformAdapter


@register(
    "astrbot_agent_wechat_bridge",
    "Codex",
    "AstrBot platform adapter for agent-wechat (1.x Native /v1 API).",
    "1.0.1",
)
class AgentWeChatBridgePlugin(Star):
    """加载平台适配器并提供远程登录与状态管理命令。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or AstrBotConfig()
        self._auto_join = AutoJoinGate()
        self._tts_disabled_umos: set[str] = set()
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

    async def initialize(self) -> None:
        """当插件在运行期被更新或热重载时，自动重启 PlatformManager 中的 agent_wechat 实例以加载新代码。"""
        pm = getattr(self.context, "platform_manager", None)
        if pm is None:
            return
        # 若 platform_insts 为空，说明当前处于 AstrBot 启动早期阶段，后续将由 CoreLifecycle 统一 initialize
        running_insts = list(getattr(pm, "platform_insts", []) or [])
        if not running_insts:
            return

        for inst in running_insts:
            try:
                meta = inst.meta() if hasattr(inst, "meta") else None
                if getattr(meta, "name", None) == "agent_wechat":
                    if hasattr(inst, "shutdown_event"):
                        inst.shutdown_event.set()
                    ws_task = getattr(inst, "ws_task", None)
                    if ws_task is not None:
                        ws_task.cancel()
            except Exception:
                pass

        for platform_cfg in list(getattr(pm, "platforms_config", []) or []):
            if not isinstance(platform_cfg, dict):
                continue
            if platform_cfg.get("type") == "agent_wechat" and platform_cfg.get("enable"):
                try:
                    await pm.reload(platform_cfg)
                    logger.info(
                        f"[agent_wechat] 已随插件热重载同步重启平台适配器: id={platform_cfg.get('id')} (v1.0.1)"
                    )
                except Exception as exc:
                    logger.warning(
                        f"[agent_wechat] 热重载平台适配器 {platform_cfg.get('id')} 失败: {exc}"
                    )

    async def terminate(self) -> None:
        AgentWeChatPlatformAdapter.set_logout_notifier(None)
        AgentWeChatPlatformAdapter.reset_logout_warning_state()
        pm = getattr(self.context, "platform_manager", None)
        if pm is None:
            return
        for inst in list(getattr(pm, "platform_insts", []) or []):
            try:
                meta = inst.meta() if hasattr(inst, "meta") else None
                if getattr(meta, "name", None) == "agent_wechat":
                    if hasattr(inst, "shutdown_event"):
                        inst.shutdown_event.set()
                    ws_task = getattr(inst, "ws_task", None)
                    if ws_task is not None:
                        ws_task.cancel()
            except Exception:
                pass

    @filter.on_llm_request(priority=100)
    async def inject_active_reply_style(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """群聊「主动接话」时补一段提示，让它像群友而不是客服。

        内核的 active_reply 只用 `event.message_str` 当 prompt，而且它的配置
        结构里根本没有 prompt 字段（core/config/default.py 的 active_reply 只有
        enable/method/possibility_reply/whitelist，存进去也会被 schema 清洗掉）。
        口吻提示因此放在插件配置里。主动接话的请求特征是「群消息 + 未被 @/唤醒」，
        正常被叫的回复 is_at_or_wake_command 为 True，不受影响。
        """
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return
        if event.is_at_or_wake_command:
            return
        style_prompt = str(self.config.get("auto_join_prompt") or "").strip()
        if not style_prompt:
            try:
                style_prompt = str(
                    self.context.get_config(umo=event.unified_msg_origin)[
                        "provider_ltm_settings"
                    ]["active_reply"].get("prompt")
                    or ""
                ).strip()
            except (KeyError, TypeError):
                style_prompt = ""
        if not style_prompt:
            return
        req.system_prompt = f"{req.system_prompt}\n\n{style_prompt}".strip()

    async def _disable_tts_for_group(self, umo: str) -> None:
        """把群会话的 TTS 关掉（每个 umo 只写一次）。

        内核结果是先 TTS 再发送，一次合成十几到几十秒，群里等着看字的人只会以为它没回。
        """
        if umo in self._tts_disabled_umos:
            return
        self._tts_disabled_umos.add(umo)
        try:
            from astrbot.core.star.session_llm_manager import SessionServiceManager

            await SessionServiceManager.set_tts_status_for_session(umo, False)
        except Exception as exc:  # 老版本没有这个接口就当没这回事
            logger.debug(f"[autojoin] 关闭群 TTS 失败 umo={umo}: {exc}")

    def _auto_join_setting(self, key: str, default: float) -> float:
        try:
            value = float(self.config.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(0.0, value)

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def auto_join_group_chat(self, event: AstrMessageEvent):
        """按群热度决定要不要主动接话，替代内核那条固定概率判定。

        内核的 need_active_reply 只看 `random() < possibility`，冷场也掷、
        自己刚说完话也掷。这里先看最近一分钟的群消息热度，过了冷场线、
        冷却期和自身静默期才掷骰。
        """
        umo = event.unified_msg_origin
        self._auto_join.observe(umo)
        # 关 TTS 要放在 @ 判断之前：被 @ 的那条同样不该回语音
        await self._disable_tts_for_group(umo)

        if event.is_at_or_wake_command:
            return
        text = (event.message_str or "").strip()
        if not text or text.startswith("/"):
            return

        if not self.config.get("auto_join_enable", True):
            return
        try:
            active_reply = self.context.get_config(umo=umo)["provider_ltm_settings"][
                "active_reply"
            ]
        except (KeyError, TypeError):
            return

        should_join, reason = self._auto_join.decide(
            umo,
            base_possibility=float(active_reply.get("possibility_reply") or 0),
            min_hotness=int(self._auto_join_setting("auto_join_min_hotness", 3)),
            cooldown_seconds=self._auto_join_setting(
                "auto_join_cooldown_seconds", 90
            ),
            self_silence_seconds=self._auto_join_setting(
                "auto_join_self_silence_seconds", 45
            ),
            question=looks_like_question(text),
        )
        logger.debug(f"[autojoin] {umo} → {reason}")
        if not should_join:
            return

        conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
        if not conv_id:
            logger.debug(f"[autojoin] {umo} 没有当前会话，跳过")
            return
        conversation = await self.context.conversation_manager.get_conversation(
            umo, conv_id
        )
        if conversation is None:
            return
        yield event.request_llm(
            prompt=text,
            session_id=event.session_id,
            conversation=conversation,
        )

    @filter.after_message_sent()
    async def mark_group_self_spoke(self, event: AstrMessageEvent) -> None:
        """每次发言后记一笔，免得刚回完话又被接话闸门放过去。"""
        self._auto_join.mark_self_spoke(event.unified_msg_origin)

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
