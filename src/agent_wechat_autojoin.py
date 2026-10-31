"""群聊「主动接话」的触发闸门。

内核那套 active_reply 只掷一次固定概率
（core/builtin_stars/astrbot/group_chat_context.py:112-131 `random.random() < ar_possibility`），
冷场、刷屏、自己刚说完话的时候都照样出手，所以观感是「随机乱入」。

这里换成看热度：先数最近一分钟有多少条群消息，再叠冷却和「自己刚说过话」的静默期，
热闹才可能接话，冷场直接闭嘴。
"""

from __future__ import annotations

import random
import time
from collections import deque

# 热度只回看最近一分钟
WINDOW_SECONDS = 60.0
# 单群最多保留的采样点，够算热度就行
MAX_TRACKED = 400

# 像在问人（而不是自言自语）的标记：问号、疑问词、征求意见
_QUESTION_MARKERS = (
    "?",
    "？",
    "吗",
    "怎么",
    "如何",
    "为什么",
    "为啥",
    "啥",
    "什么",
    "哪",
    "多少",
    "能不能",
    "可不可以",
    "有没有",
    "是不是",
    "要不要",
    "行不行",
    "咋",
    "请问",
    "意见",
    "建议",
    "觉得",
)
# 直接喊 bot 的名字
_BOT_CALLS = ("肥鱼", "机器人", "bot")
# 对着人说话的第二人称
_SECOND_PERSON = ("你", "您")


def looks_like_question(text: str) -> bool:
    """粗略判断这条消息是不是在向人提问/要意见。

    群聊冷场时默认沉默，但「他在问」这类消息要接住，否则观感就是装死。
    """
    body = (text or "").strip()
    if not body:
        return False
    if any(marker in body for marker in _QUESTION_MARKERS):
        return True
    if any(name in body.lower() for name in _BOT_CALLS):
        return True
    return any(marker in body for marker in _SECOND_PERSON)


class AutoJoinGate:
    """按群（umo）维护最近消息时间线，决定这一次要不要接话。"""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = {}
        self._last_self_speak: dict[str, float] = {}
        self._last_join: dict[str, float] = {}

    def observe(self, umo: str) -> None:
        """记一条群消息，用于算热度。"""
        now = time.monotonic()
        events = self._events.setdefault(umo, deque(maxlen=MAX_TRACKED))
        events.append(now)
        while events and now - events[0] > WINDOW_SECONDS * 2:
            events.popleft()

    def mark_self_spoke(self, umo: str) -> None:
        """记录本群刚发过言（含被 @ 的正常回复）。"""
        self._last_self_speak[umo] = time.monotonic()

    def hotness(self, umo: str) -> int:
        now = time.monotonic()
        return sum(1 for ts in self._events.get(umo, ()) if now - ts <= WINDOW_SECONDS)

    def decide(
        self,
        umo: str,
        *,
        base_possibility: float,
        min_hotness: int,
        cooldown_seconds: float,
        self_silence_seconds: float,
        question: bool = False,
    ) -> tuple[bool, str]:
        """返回 (是否接话, 判定说明)。说明进日志，方便对着看为什么没说话。

        question=True 表示这条像在向人提问：冷场线和冷却都放宽，且不再掷骰子，
        但冷却/静默仍生效，免得连着追问时刷屏。
        """
        now = time.monotonic()
        hot = self.hotness(umo)
        if question:
            min_hotness = min(min_hotness, 1)
            cooldown_seconds = min(cooldown_seconds, 15.0)
            self_silence_seconds = min(self_silence_seconds, 15.0)

        if hot < min_hotness:
            return False, f"冷场 hot={hot} < {min_hotness}"

        since_join = now - self._last_join.get(umo, 0.0)
        if since_join < cooldown_seconds:
            return False, f"冷却中 {since_join:.0f}s < {cooldown_seconds:.0f}s"

        since_self = now - self._last_self_speak.get(umo, 0.0)
        if since_self < self_silence_seconds:
            return False, f"自己刚说过话 {since_self:.0f}s < {self_silence_seconds:.0f}s"

        if question:
            self._last_join[umo] = now
            return True, f"提问接入 hot={hot}"

        # 热度越高越敢插话：min_hotness 时 0.5 倍，两倍热度时封顶 1.3 倍
        factor = min(1.3, max(0.5, hot / (min_hotness * 2)))
        chance = min(0.6, max(0.0, base_possibility * factor))
        if random.random() >= chance:
            return False, f"没掷中 hot={hot} p={chance:.2f}"

        self._last_join[umo] = now
        return True, f"接话 hot={hot} p={chance:.2f}"
