"""平台适配器使用的消息事件对象（适配 agent-wechat 1.x Native /v1 API）。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import mimetypes
import os
import re
import time
import unicodedata
from collections.abc import AsyncGenerator
from io import BytesIO
from typing import Any

import requests

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import (
    At,
    AtAll,
    File,
    Image,
    Node,
    Nodes,
    Plain,
    Record,
    Reply,
    Share,
    Video,
)
from astrbot.api.platform import Group, MessageMember

from .agent_wechat_client import WeChatClient

SEND_REQUEST_TIMEOUT_SECONDS = 120.0
SEND_LOG_PREFIX = "[agent_wechat][send]"
SEND_RECOVERY_RETRY_ATTEMPTS = 2
SEND_RECOVERY_RETRY_INTERVAL_SECONDS = 1.0
IGNORED_SERIALIZED_SEG_TYPES = {"reply"}
MAX_FILENAME_LENGTH = 96
OUTBOUND_IMAGE_DEDUP_WINDOW_SECONDS = 8.0
WECHAT_MENTION_ID_RE = re.compile(r"^[A-Za-z0-9_@.-]{1,256}$")


def _component_type_name(component: Any) -> str:
    return type(component).__name__


def _guess_mime_type(path: str, fallback: str = "application/octet-stream") -> str:
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type or fallback


def _basename_from_url(url: str, default: str = "file") -> str:
    name = url.rstrip("/").split("/")[-1].split("?")[0]
    return name or default


def _extract_segment_source(seg_data: dict[str, Any]) -> str | None:
    for key in ("file", "url", "path", "src", "local_path", "temp_file"):
        value = seg_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested_key in ("file", "url", "path", "src"):
                nested = value.get(nested_key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return None


def _extract_segment_filename(seg_data: dict[str, Any], fallback: str = "file") -> str:
    for key in ("name", "filename", "file_name", "title"):
        value = seg_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _sanitize_filename(name: str, fallback: str = "file.bin") -> str:
    original = (name or "").strip()
    if not original:
        original = fallback
    original = os.path.basename(original).replace("\x00", "")
    stem, ext = os.path.splitext(original)
    ext = (ext or ".bin")[:16]
    normalized = unicodedata.normalize("NFKD", stem)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_name).strip("._-")
    if not safe:
        safe = "file"
    max_stem_len = max(8, MAX_FILENAME_LENGTH - len(ext))
    safe = safe[:max_stem_len]
    return f"{safe}{ext}"


def _normalize_image_for_wechat(data: bytes, mime_type: str) -> tuple[bytes, str]:
    lower_mime = (mime_type or "").lower()
    if lower_mime in {"image/png", "image/jpeg", "image/jpg", "image/gif"}:
        return data, ("image/jpeg" if lower_mime == "image/jpg" else lower_mime)
    if PILImage is None:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return data, "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return data, "image/jpeg"
        return data, mime_type or "image/png"
    try:
        with PILImage.open(BytesIO(data)) as img:
            if img.mode not in {"RGB", "RGBA"}:
                img = img.convert("RGBA")
            output = BytesIO()
            img.save(output, format="PNG")
            return output.getvalue(), "image/png"
    except Exception:
        logger.warning(
            f"{SEND_LOG_PREFIX} failed to normalize image mime={mime_type}, keep original bytes"
        )
        return data, mime_type or "image/png"


async def _segment_to_dict(seg: Any) -> dict[str, Any] | None:
    if isinstance(seg, dict):
        return seg
    if not hasattr(seg, "to_dict"):
        return None
    serialized = seg.to_dict()
    if inspect.isawaitable(serialized):
        serialized = await serialized
    if isinstance(serialized, dict):
        return serialized
    return None


def _load_binary_from_path(
    path: str,
    timeout: int = 30,
    fallback_mime: str = "application/octet-stream",
) -> tuple[bytes, str, str]:
    if path.startswith("base64://"):
        encoded = path.split("://", 1)[1].lstrip("/")
        data = base64.b64decode(encoded)
        return data, fallback_mime, "inline.bin"

    if path.startswith("data:"):
        header, _, encoded = path.partition(",")
        mime_part = header[5:].split(";", 1)[0].strip() or fallback_mime
        data = base64.b64decode(encoded)
        ext = mimetypes.guess_extension(mime_part) or ".bin"
        return data, mime_part, f"inline{ext}"

    if path.startswith(("http://", "https://")):
        response = requests.get(path, timeout=timeout)
        response.raise_for_status()
        mime_type = (
            (response.headers.get("Content-Type") or "")
            .split(";", 1)[0]
            .strip()
            or _guess_mime_type(path, fallback_mime)
        )
        return response.content, mime_type, _basename_from_url(path)

    normalized = path[7:] if path.startswith("file://") else path
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    with open(normalized, "rb") as handle:
        data = handle.read()
    return (
        data,
        _guess_mime_type(normalized, fallback_mime),
        os.path.basename(normalized),
    )


def _media_payload_entry(
    chat_id: str,
    seg_type: str,
    data: bytes,
    mime_type: str,
    filename: str,
) -> dict[str, Any]:
    encoded = base64.b64encode(data).decode("utf-8")
    safe_filename = _sanitize_filename(filename)
    lower_type = seg_type.lower()
    if lower_type in {"record", "audio", "voice"}:
        return {
            "chatId": chat_id,
            "voice": {
                "data": encoded,
                "filename": safe_filename,
                "mimeType": mime_type or "audio/wav",
            },
            "file": {
                "data": encoded,
                "filename": safe_filename,
                "mimeType": mime_type or "audio/wav",
                "kind": "voice",
            },
        }
    if lower_type == "video":
        return {
            "chatId": chat_id,
            "file": {
                "data": encoded,
                "filename": safe_filename,
                "mimeType": mime_type or "video/mp4",
                "kind": "video",
            },
        }
    return {
        "chatId": chat_id,
        "file": {
            "data": encoded,
            "filename": safe_filename,
            "mimeType": mime_type or "application/octet-stream",
            "kind": "file",
        },
    }


def parse_quote_reference(reply_id: Any) -> dict[str, str] | None:
    """从 Reply.id 中解析出 agent-wechat 所需的 {messageId, serverId}。"""
    raw = str(reply_id or "").strip()
    if not raw:
        return None
    if "|" in raw:
        msg_id, srv_id = raw.rsplit("|", 1)
        if msg_id and srv_id.isdigit() and int(srv_id) > 0:
            return {"messageId": msg_id, "serverId": srv_id}
    return None


class AgentWeChatMessageEvent(AstrMessageEvent):
    """单条微信入站消息对应的事件封装。"""

    _recent_outbound_image_signatures: dict[str, float] = {}
    _outbound_image_dedup_lock = asyncio.Lock()

    def __init__(
        self,
        message_str: str,
        message_obj: Any,
        platform_meta: Any,
        session_id: str,
        client: WeChatClient,
        chat_id: str,
        is_group: bool,
    ) -> None:
        super().__init__(
            message_str=message_str,
            message_obj=message_obj,
            platform_meta=platform_meta,
            session_id=session_id,
        )
        self.client = client
        self.chat_id = chat_id
        self.is_group = is_group

    @staticmethod
    def _push_text(buffer: list[str], value: str | None) -> None:
        if value:
            buffer.append(value)

    @staticmethod
    def _at_to_text(component: At) -> str:
        name = (
            getattr(component, "name", None) or getattr(component, "qq", None) or "user"
        )
        return f"@{name}"

    @classmethod
    async def _build_send_payloads(
        cls,
        chat_id: str,
        message_chain: MessageChain,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        text_buffer: list[str] = []
        mention_user_ids: list[str] = []
        mention_all = False
        pending_quote: dict[str, str] | None = None
        is_group = str(chat_id).endswith("@chatroom")

        def flush_text_only() -> None:
            nonlocal mention_all, pending_quote
            if not text_buffer and not mention_user_ids and not mention_all:
                return
            text = "".join(text_buffer)
            if not text and (mention_user_ids or mention_all):
                text = " "
            if text != "":
                item: dict[str, Any] = {"chatId": chat_id, "text": text}
                if pending_quote and not mention_user_ids and not mention_all:
                    item["quote"] = pending_quote
                elif is_group and (mention_all or mention_user_ids):
                    if mention_all:
                        item["mentions"] = {"all": True}
                    else:
                        item["mentions"] = {"userIds": list(mention_user_ids)}
                payloads.append(item)
            text_buffer.clear()
            mention_user_ids.clear()
            mention_all = False
            pending_quote = None

        async def expand_serialized_nodes(serialized: dict[str, Any]) -> int:
            messages = serialized.get("messages")
            if not isinstance(messages, list):
                return 0

            flush_text_only()
            expanded = 0
            for node_item in messages:
                if not isinstance(node_item, dict):
                    continue
                node_data = node_item.get("data")
                if not isinstance(node_data, dict):
                    continue
                content = node_data.get("content")
                if not isinstance(content, list):
                    continue

                node_text_parts: list[str] = []

                def flush_node_text() -> None:
                    nonlocal expanded
                    if not node_text_parts:
                        return
                    node_text = "".join(node_text_parts)
                    if node_text != "":
                        payloads.append({"chatId": chat_id, "text": node_text})
                        expanded += 1
                    node_text_parts.clear()

                for seg_raw in content:
                    seg = await _segment_to_dict(seg_raw)
                    if not isinstance(seg, dict):
                        continue
                    seg_type = str(seg.get("type") or "").lower()
                    seg_data = seg.get("data")
                    if not isinstance(seg_data, dict):
                        continue

                    if seg_type in {"text", "plain"}:
                        seg_text = str(seg_data.get("text") or "")
                        if seg_text != "":
                            node_text_parts.append(seg_text)
                        continue

                    if seg_type == "image":
                        source = _extract_segment_source(seg_data)
                        if not source:
                            logger.warning(
                                f"{SEND_LOG_PREFIX} skip serialized node image without source "
                                f"chat={chat_id}"
                            )
                            continue
                        flush_node_text()
                        data, mime_type, _ = await asyncio.to_thread(
                            _load_binary_from_path,
                            str(source),
                            30,
                            "image/png",
                        )
                        image_data, normalized_mime = await asyncio.to_thread(
                            _normalize_image_for_wechat,
                            data,
                            mime_type or "image/png",
                        )
                        payloads.append(
                            {
                                "chatId": chat_id,
                                "image": {
                                    "data": base64.b64encode(image_data).decode(
                                        "utf-8"
                                    ),
                                    "mimeType": normalized_mime or "image/png",
                                },
                            }
                        )
                        expanded += 1
                        continue

                    if seg_type in {"video", "file", "record", "audio"}:
                        source = _extract_segment_source(seg_data)
                        if not source:
                            logger.warning(
                                f"{SEND_LOG_PREFIX} skip serialized node file without source "
                                f"chat={chat_id} seg_type={seg_type}"
                            )
                            continue
                        flush_node_text()
                        data, _, filename = await asyncio.to_thread(
                            _load_binary_from_path, str(source)
                        )
                        safe_filename = _sanitize_filename(
                            _extract_segment_filename(
                                seg_data, fallback=str(filename or "file.bin")
                            )
                        )
                        payloads.append(
                            {
                                "chatId": chat_id,
                                "file": {
                                    "data": base64.b64encode(data).decode("utf-8"),
                                    "filename": safe_filename,
                                },
                            }
                        )
                        expanded += 1
                        continue

                flush_node_text()

            return expanded

        async def expand_node_component(component: Node | Nodes) -> int:
            flush_text_only()

            node_items = (
                list(getattr(component, "nodes", []) or [])
                if isinstance(component, Nodes)
                else [component]
            )
            expanded = 0
            for node_item in node_items:
                node_content = list(getattr(node_item, "content", []) or [])
                if not node_content:
                    continue
                nested_payloads = await cls._build_send_payloads(
                    chat_id, MessageChain(node_content)
                )
                if not nested_payloads:
                    continue
                payloads.extend(nested_payloads)
                expanded += len(nested_payloads)
            return expanded

        for component in message_chain.chain:
            if isinstance(component, (Nodes, Node)):
                expanded = await expand_node_component(component)
                if expanded > 0:
                    continue

            if isinstance(component, Reply):
                quote_ref = parse_quote_reference(getattr(component, "id", None))
                if quote_ref:
                    pending_quote = quote_ref
                continue

            if isinstance(component, Share):
                flush_text_only()
                url = str(getattr(component, "url", "") or "").strip()
                title = str(getattr(component, "title", "") or url or "分享").strip()
                if url:
                    share_payload: dict[str, Any] = {
                        "chatId": chat_id,
                        "kind": "share",
                        "title": title,
                        "url": url,
                    }
                    content_desc = str(getattr(component, "content", "") or "").strip()
                    if content_desc:
                        share_payload["description"] = content_desc
                    thumb = str(getattr(component, "image", "") or "").strip()
                    if thumb.startswith(("http://", "https://")):
                        share_payload["thumbnailUrl"] = thumb
                    payloads.append(share_payload)
                continue

            if isinstance(component, Plain):
                cls._push_text(text_buffer, component.text)
                continue

            if isinstance(component, AtAll):
                if is_group:
                    mention_all = True
                else:
                    cls._push_text(text_buffer, "@所有人 ")
                continue

            if isinstance(component, At):
                qq = str(getattr(component, "qq", "") or "").strip()
                if is_group and qq in {"all", "notify@all"}:
                    mention_all = True
                elif (
                    is_group
                    and qq
                    and WECHAT_MENTION_ID_RE.match(qq)
                    and getattr(component, "name", None)
                ):
                    if qq not in mention_user_ids:
                        mention_user_ids.append(qq)
                else:
                    cls._push_text(text_buffer, cls._at_to_text(component))
                    cls._push_text(text_buffer, " ")
                continue

            if isinstance(component, Image):
                source = getattr(component, "file", None) or getattr(
                    component, "url", None
                )
                if not source:
                    logger.warning(
                        f"{SEND_LOG_PREFIX} skip image component without source chat={chat_id}"
                    )
                    continue
                data, mime_type, _ = await asyncio.to_thread(
                    _load_binary_from_path, source, 30, "image/png"
                )
                image_data, normalized_mime = await asyncio.to_thread(
                    _normalize_image_for_wechat, data, mime_type or "image/png"
                )
                flush_text_only()
                payloads.append(
                    {
                        "chatId": chat_id,
                        "image": {
                            "data": base64.b64encode(image_data).decode("utf-8"),
                            "mimeType": normalized_mime or "image/png",
                        },
                    }
                )
                continue

            if isinstance(component, Record):
                source = (
                    getattr(component, "file", None)
                    or getattr(component, "url", None)
                    or getattr(component, "path", None)
                )
                if not source:
                    logger.warning(
                        f"{SEND_LOG_PREFIX} skip record component without source chat={chat_id}"
                    )
                    continue
                # 优先使用 AstrBot Record.convert_to_file_path 转为 wav，以便 agent-wechat 发送原生语音条
                resolved_source = str(source)
                if hasattr(component, "convert_to_file_path"):
                    try:
                        converted_path = await component.convert_to_file_path()
                        if converted_path and os.path.exists(converted_path):
                            resolved_source = converted_path
                    except Exception:
                        pass
                data, mime_type, filename = await asyncio.to_thread(
                    _load_binary_from_path, resolved_source, 30, "audio/wav"
                )
                safe_filename = _sanitize_filename(
                    str(getattr(component, "name", None) or filename or "voice.wav")
                )
                flush_text_only()
                payloads.append(
                    {
                        "chatId": chat_id,
                        "file": {
                            "data": base64.b64encode(data).decode("utf-8"),
                            "filename": safe_filename,
                        },
                        "voice": {
                            "data": base64.b64encode(data).decode("utf-8"),
                            "filename": safe_filename,
                            "mimeType": mime_type or "audio/wav",
                        },
                    }
                )
                continue

            if isinstance(component, (File, Video)):
                source = getattr(component, "file", None) or getattr(
                    component, "url", None
                )
                if not source and hasattr(component, "get_file"):
                    try:
                        source = await component.get_file()
                    except Exception:
                        source = None
                if not source:
                    logger.warning(
                        f"{SEND_LOG_PREFIX} skip file/video component without source "
                        f"chat={chat_id}"
                    )
                    continue
                data, _, filename = await asyncio.to_thread(
                    _load_binary_from_path, str(source)
                )
                safe_filename = _sanitize_filename(
                    str(getattr(component, "name", None) or filename or "file.bin")
                )
                flush_text_only()
                payloads.append(
                    {
                        "chatId": chat_id,
                        "file": {
                            "data": base64.b64encode(data).decode("utf-8"),
                            "filename": safe_filename,
                        },
                    }
                )
                continue

            serialized = component.to_dict() if hasattr(component, "to_dict") else None
            if inspect.isawaitable(serialized):
                serialized = await serialized

            if isinstance(serialized, dict):
                seg_type = str(serialized.get("type") or "").lower()
                if seg_type in IGNORED_SERIALIZED_SEG_TYPES:
                    continue
                seg_data = serialized.get("data")
                if isinstance(seg_data, dict) and seg_type in {
                    "video",
                    "file",
                    "record",
                    "audio",
                }:
                    source = _extract_segment_source(seg_data)
                    if source:
                        flush_text_only()
                        data, _, filename = await asyncio.to_thread(
                            _load_binary_from_path, str(source)
                        )
                        safe_filename = _sanitize_filename(
                            _extract_segment_filename(
                                seg_data, fallback=str(filename or "file.bin")
                            )
                        )
                        payloads.append(
                            {
                                "chatId": chat_id,
                                "file": {
                                    "data": base64.b64encode(data).decode("utf-8"),
                                    "filename": safe_filename,
                                },
                            }
                        )
                        continue

                expanded = await expand_serialized_nodes(serialized)
                if expanded > 0:
                    continue
                cls._push_text(text_buffer, str(serialized))
                continue

            logger.warning(
                f"{SEND_LOG_PREFIX} unsupported component ignored "
                f"chat={chat_id} component={_component_type_name(component)}"
            )

        flush_text_only()
        return payloads

    @classmethod
    def _build_outbound_image_signature(
        cls,
        chat_id: str,
        mime_type: str,
        base64_data: str,
    ) -> str:
        digest = hashlib.sha256(base64_data.encode("utf-8")).hexdigest()
        return f"{chat_id}:{mime_type}:{digest}"

    @classmethod
    async def _should_skip_duplicate_image_payload(
        cls,
        chat_id: str,
        payload: dict[str, Any],
    ) -> bool:
        image = payload.get("image")
        if not isinstance(image, dict):
            return False

        base64_data = str(image.get("data") or "")
        if not base64_data:
            return False

        mime_type = str(image.get("mimeType") or "image/png")
        signature = cls._build_outbound_image_signature(chat_id, mime_type, base64_data)
        now = time.monotonic()

        async with cls._outbound_image_dedup_lock:
            stale_keys = [
                key
                for key, ts in cls._recent_outbound_image_signatures.items()
                if now - ts > OUTBOUND_IMAGE_DEDUP_WINDOW_SECONDS
            ]
            for key in stale_keys:
                cls._recent_outbound_image_signatures.pop(key, None)

            if signature in cls._recent_outbound_image_signatures:
                logger.info(
                    f"{SEND_LOG_PREFIX} skip duplicate outbound image "
                    f"chat={chat_id} dedup_window={OUTBOUND_IMAGE_DEDUP_WINDOW_SECONDS:.1f}s"
                )
                return True

            cls._recent_outbound_image_signatures[signature] = now
            return False

    @classmethod
    async def send_message_chain(
        cls,
        client: WeChatClient,
        chat_id: str,
        message_chain: MessageChain,
    ) -> None:
        payloads = await cls._build_send_payloads(chat_id, message_chain)
        if not payloads:
            return

        filtered_payloads: list[dict[str, Any]] = []
        for payload in payloads:
            if await cls._should_skip_duplicate_image_payload(chat_id, payload):
                continue
            filtered_payloads.append(payload)

        if not filtered_payloads:
            return

        for idx, payload in enumerate(filtered_payloads):
            try:
                result = await asyncio.to_thread(
                    client.send_message,
                    payload,
                    timeout=SEND_REQUEST_TIMEOUT_SECONDS,
                )
            except requests.exceptions.ReadTimeout as exc:
                logger.exception(
                    f"{SEND_LOG_PREFIX} send payload timeout "
                    f"chat={chat_id} idx={idx + 1}/{len(filtered_payloads)}"
                )
                raise RuntimeError(
                    "agent-wechat 发送超时，请检查远端 agent-wechat 守护进程连接状态。"
                ) from exc
            except requests.exceptions.RequestException as exc:
                logger.exception(
                    f"{SEND_LOG_PREFIX} send payload request error "
                    f"chat={chat_id} idx={idx + 1}/{len(filtered_payloads)}"
                )
                raise RuntimeError(f"agent-wechat 发送请求失败: {exc}") from exc

            if result.get("success", True):
                continue

            error = str(result.get("error") or "")
            logger.error(
                f"{SEND_LOG_PREFIX} send payload failed "
                f"chat={chat_id} idx={idx + 1}/{len(filtered_payloads)} "
                f"error={error}"
            )
            raise RuntimeError(error or "agent-wechat 发送失败")

    async def send(self, message: MessageChain) -> None:
        await self.send_message_chain(self.client, self.chat_id, message)
        await super().send(message)

    async def send_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        use_fallback: bool = False,
    ) -> None:
        if use_fallback:
            async for chain in generator:
                await self.send(chain)
                await asyncio.sleep(1.2)
            return

        parts: list[str] = []
        async for chain in generator:
            for component in chain.chain:
                if isinstance(component, Plain):
                    parts.append(component.text)

        if parts:
            await self.send(MessageChain([Plain(text="".join(parts))]))

    async def get_group(
        self, group_id: str | None = None, **kwargs: Any
    ) -> Group | None:
        if not self.is_group and not group_id:
            return None
        raw_group_id = str(
            group_id or getattr(self.message_obj, "group_id", None) or self.chat_id
        )
        group = Group(raw_group_id)
        group.group_name = getattr(
            getattr(self.message_obj, "group", None), "group_name", None
        )
        try:
            info = await asyncio.to_thread(self.client.get_group, raw_group_id)
            if isinstance(info, dict):
                group.group_name = str(
                    info.get("displayName")
                    or info.get("name")
                    or group.group_name
                    or raw_group_id
                )
                if info.get("ownerId"):
                    group.group_owner = str(info["ownerId"])
                if isinstance(info.get("administratorIds"), list):
                    group.group_admins = [str(x) for x in info["administratorIds"] if x]
            members_page = await asyncio.to_thread(
                self.client.list_group_members, raw_group_id, 200
            )
            items = (
                members_page.get("items") if isinstance(members_page, dict) else None
            )
            if isinstance(items, list):
                members: list[MessageMember] = []
                for m in items:
                    if not isinstance(m, dict) or m.get("deleted") is True:
                        continue
                    uid = str(m.get("username") or m.get("id") or "")
                    if not uid:
                        continue
                    nick = str(
                        m.get("roomDisplayName")
                        or m.get("displayName")
                        or m.get("mentionName")
                        or uid
                    )
                    members.append(MessageMember(user_id=uid, nickname=nick))
                group.members = members
                group.member_count = len(members)
        except Exception as exc:
            logger.debug(
                f"[agent_wechat] get_group enrichment skipped for {raw_group_id}: {exc}"
            )
        return group
