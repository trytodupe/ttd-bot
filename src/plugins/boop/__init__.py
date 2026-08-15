"""Boop plugin — send a playful private "Boop!" message to a group member.

Usage in group chat:
    /boop @target  — send "Boop!" as a private message to the target

Rules:
    - Exactly one @target segment is required.
    - Sender cannot boop themselves.
    - 10-second monotonic cooldown per sender (not per receiver).
    - A failed private-message API call does NOT consume the cooldown.
"""

from __future__ import annotations

import asyncio
import re as _re
import time as _time

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

__plugin_meta__ = PluginMetadata(
    name="boop",
    description="给群友发送一条私聊 Boop! 消息。",
    usage="/boop @目标",
)

_COOLDOWN_SECONDS = 10
_COOLDOWN_NOTICE = "你刚 Boop 过别人，冷却中哦~"

_cooldowns: dict[int, float] = {}
_sender_locks: dict[int, asyncio.Lock] = {}

_RE_BOOP = _re.compile(r"^/boop(?:\s|$)")


def _is_boop_command(event: GroupMessageEvent) -> bool:
    text = event.get_message().extract_plain_text().strip()
    return bool(_RE_BOOP.match(text))


_matcher = on_message(
    rule=Rule(_is_boop_command),
    priority=10,
    block=True,
)


def _extract_at_targets(message: Message) -> list[int]:
    targets: list[int] = []
    for seg in message:
        if getattr(seg, "type", None) == "at":
            try:
                targets.append(int(seg.data.get("qq", 0)))
            except (ValueError, TypeError):
                pass
    return targets


def _get_sender_lock(user_id: int) -> asyncio.Lock:
    lock = _sender_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _sender_locks[user_id] = lock
    return lock


@_matcher.handle()
async def _handle_boop(bot: Bot, event: GroupMessageEvent, matcher: Matcher) -> None:
    at_targets = _extract_at_targets(event.message)

    # --- Validation ---
    if not at_targets:
        await matcher.finish(message="用法：/boop @目标（请 @ 一个用户）")
        return

    if len(at_targets) > 1:
        await matcher.finish(message="一次只能 Boop 一个用户哦~")
        return

    target_qq = at_targets[0]

    # Self-target check
    if target_qq == event.user_id:
        await matcher.finish(message="你不能 Boop 自己哦~")
        return

    # Serialize the cooldown check and delivery per sender so concurrent
    # commands cannot all pass before the first successful send is recorded.
    async with _get_sender_lock(event.user_id):
        now = _time.monotonic()
        last_used = _cooldowns.get(event.user_id)
        if last_used is not None and now - last_used < _COOLDOWN_SECONDS:
            await matcher.finish(message=_COOLDOWN_NOTICE)
            return

        try:
            await bot.call_api("send_private_msg", user_id=target_qq, message="Boop!")
        except Exception:
            await matcher.finish(message="私聊发送失败，可能对方未开启私聊权限。")
            return

        # Cooldown begins only after a successful send.
        _cooldowns[event.user_id] = _time.monotonic()

    await matcher.finish(message=f"Boop! 已发送给 {target_qq}")
