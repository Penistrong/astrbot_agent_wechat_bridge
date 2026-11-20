"""访问控制、会话分类与文本归一化辅助函数。"""

from __future__ import annotations

import re
from typing import Any

DM_POLICIES = {"open", "allowlist", "disabled"}
GROUP_POLICIES = {"open", "allowlist", "disabled"}
INVISIBLE_TEXT_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f]")
MENTION_SPLIT_RE = re.compile(r"[\u2005\s]+")

SYSTEM_PEER_IDS = {
    "weixin",
    "fmessage",
    "medianote",
    "floatbottle",
    "newsapp",
    "qqmail",
    "tmessage",
    "qmessage",
    "weibo",
}


def normalize_wechat_id(raw: str | None) -> str:
    if not raw:
        return ""
    value = raw.strip()
    for prefix in ("wechat:group:", "wechat:"):
        if value.startswith(prefix):
            value = value[len(prefix) :].strip()
    return value


def normalize_allowlist(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        normalized = normalize_wechat_id(str(value))
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def strip_leading_mentions(text: str) -> str:
    """移除消息开头的微信 @提及，仅保留真正正文。"""

    cleaned = INVISIBLE_TEXT_RE.sub("", text or "").strip()
    if not cleaned:
        return ""

    parts = [part.strip() for part in MENTION_SPLIT_RE.split(cleaned) if part.strip()]
    while parts and parts[0].startswith(("@", "＠")):
        parts.pop(0)
    if parts:
        return " ".join(parts).strip()
    return cleaned


def strip_leading_self_mentions(
    text: str,
    aliases: list[str] | set[str] | tuple[str, ...],
    *,
    strip_first_if_unmatched: bool = False,
) -> str:
    """仅移除消息开头指向机器人自身的 @提及，保留对其他群成员的 @提及。

    当整条消息仅包含对机器人的 @提及（空 @）时返回空字符串 ``""``。
    """

    cleaned = INVISIBLE_TEXT_RE.sub("", text or "").strip()
    if not cleaned:
        return ""

    raw_aliases = sorted(
        {
            INVISIBLE_TEXT_RE.sub("", str(item)).strip().lstrip("@＠").strip()
            for item in (aliases or ())
            if item and str(item).strip().lstrip("@＠").strip()
        },
        key=len,
        reverse=True,
    )
    normalized_aliases = {
        _normalize_mention_target(item) for item in raw_aliases if item
    }
    normalized_aliases.discard("")

    remaining = cleaned
    stripped_any = False

    while remaining.startswith(("@", "＠")):
        body = remaining[1:]
        matched_len = 0
        for alias in raw_aliases:
            if body.lower().startswith(alias.lower()):
                end_pos = len(alias)
                if end_pos == len(body) or body[end_pos] in ("\u2005", " ", "\t", "\n", "\r"):
                    matched_len = end_pos
                    break

        if matched_len > 0:
            remaining = body[matched_len:].lstrip("\u2005 \t\r\n")
            stripped_any = True
            continue

        # 按分隔符拆分首个 @token 再比对归一化别名
        token_match = re.match(r"^[@＠]([^\u2005\s]+)(?:[\u2005\s]+(.*))?$", remaining, re.DOTALL)
        if token_match:
            target_norm = _normalize_mention_target(token_match.group(1))
            if target_norm and target_norm in normalized_aliases:
                remaining = (token_match.group(2) or "").lstrip("\u2005 \t\r\n")
                stripped_any = True
                continue

        break

    if not stripped_any and strip_first_if_unmatched and remaining.startswith(("@", "＠")):
        # 已知 mentions.self=True 且未提及其他群成员，但机器人群名片尚未进入 aliases 缓存
        u2005_match = re.match(r"^[@＠][^\u2005]+\u2005(.*)$", remaining, re.DOTALL)
        if u2005_match:
            remaining = u2005_match.group(1).lstrip("\u2005 \t\r\n")
        else:
            ws_match = re.match(r"^[@＠]\S+(?:\s+(.*))?$", remaining, re.DOTALL)
            if ws_match:
                remaining = (ws_match.group(1) or "").lstrip("\u2005 \t\r\n")

    return remaining.replace("\u2005", " ").strip()


def _normalize_mention_target(raw: str | None) -> str:
    if not raw:
        return ""
    cleaned = INVISIBLE_TEXT_RE.sub("", str(raw)).strip()
    if cleaned.startswith(("@", "＠")):
        cleaned = cleaned[1:]
    return cleaned.strip().casefold()


def extract_leading_mentions(text: str) -> list[str]:
    """提取消息开头连续 @ 提及目标（已归一化）。"""

    cleaned = INVISIBLE_TEXT_RE.sub("", text or "").strip()
    if not cleaned:
        return []

    parts = [part.strip() for part in MENTION_SPLIT_RE.split(cleaned) if part.strip()]
    mentions: list[str] = []
    for part in parts:
        if not part.startswith(("@", "＠")):
            break
        target = _normalize_mention_target(part)
        if target:
            mentions.append(target)
    return mentions


def is_leading_self_mention(
    text: str, aliases: list[str] | set[str] | tuple[str, ...]
) -> bool:
    """判断消息开头是否 @ 了机器人本人（按别名集合匹配）。"""

    mention_targets = extract_leading_mentions(text)
    if not mention_targets:
        return False

    normalized_aliases = {
        alias
        for alias in (_normalize_mention_target(item) for item in aliases)
        if alias
    }
    if not normalized_aliases:
        return False

    return any(target in normalized_aliases for target in mention_targets)


def is_native_mentioned(
    mentions: dict[str, Any] | None,
    raw_text: str,
    aliases: list[str] | set[str] | tuple[str, ...],
) -> bool:
    """结合 agent-wechat v1 NativeMentions 元数据与文本前缀判断是否提及了机器人。"""

    if isinstance(mentions, dict):
        state = str(mentions.get("state") or "")
        if state == "known":
            if mentions.get("self") is True or mentions.get("all") is True:
                return True
        user_ids = mentions.get("userIds")
        if isinstance(user_ids, list) and user_ids and aliases:
            normalized_aliases = {
                _normalize_mention_target(item) for item in aliases if item
            }
            normalized_aliases.discard("")
            for uid in user_ids:
                if _normalize_mention_target(str(uid)) in normalized_aliases:
                    return True

    if raw_text and aliases:
        return is_leading_self_mention(raw_text, aliases)
    return False


def is_official_account(
    chat_id: str | None,
    classification: dict[str, Any] | None = None,
) -> bool:
    """判断是否为公众号、服务号或系统内置账号（保留 filehelper 供测试与自用）。"""

    normalized = normalize_wechat_id(chat_id)
    if not normalized:
        return False
    if normalized == "filehelper":
        return False
    if normalized.startswith("gh_") or normalized in SYSTEM_PEER_IDS:
        return True

    if isinstance(classification, dict):
        category = str(classification.get("category") or "").lower()
        if category in {"business", "system"}:
            return True
        business_kind = str(classification.get("businessKind") or "").lower()
        if business_kind in {"official_account", "service_account"}:
            return True

    return False


def is_group_chat(chat_id: str | None, thread_kind: str | None = None) -> bool:
    if thread_kind and str(thread_kind).lower() == "group":
        return True
    return normalize_wechat_id(chat_id).endswith("@chatroom")


def is_sender_allowed(sender_id: str | None, allowlist: list[str]) -> bool:
    if "*" in allowlist:
        return True
    normalized_sender = normalize_wechat_id(sender_id)
    if not normalized_sender:
        return False
    return normalized_sender in allowlist


def should_forward_message(
    *,
    is_group: bool,
    sender_id: str | None,
    was_mentioned: bool,
    require_mention: bool,
    dm_policy: str,
    dm_allowlist: list[str],
    group_policy: str,
    group_allowlist: list[str],
) -> tuple[bool, str]:
    """返回消息是否应转发到机器人框架。"""

    dm_policy = dm_policy if dm_policy in DM_POLICIES else "disabled"
    group_policy = group_policy if group_policy in GROUP_POLICIES else "disabled"

    if is_group:
        if group_policy == "disabled":
            return False, "group_policy_disabled"
        if group_policy == "allowlist" and not is_sender_allowed(
            sender_id, group_allowlist
        ):
            return False, "group_sender_not_allowlisted"
        if require_mention and not was_mentioned:
            return False, "mention_required"
        return True, f"group_policy_{group_policy}"

    if dm_policy == "disabled":
        return False, "dm_policy_disabled"
    if dm_policy == "allowlist" and not is_sender_allowed(sender_id, dm_allowlist):
        return False, "dm_sender_not_allowlisted"
    return True, f"dm_policy_{dm_policy}"
