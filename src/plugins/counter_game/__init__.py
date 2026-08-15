from __future__ import annotations

import asyncio
import re

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, is_type

__plugin_meta__ = PluginMetadata(
    name="counter-game",
    description="群共享接龙游戏。任何人发送纯数字消息参与，群内从 1 开始递增接龙。",
    usage="发送纯数字消息参与接龙。",
)

_NUMBER_RE = re.compile(r"^\d+$")
_SUCCESS_EMOJI_ID = "76"

# Per-group previous accepted value.  Starts at 0 so the first valid number is 1.
_group_counters: dict[int, int] = {}
# Per-group locks: state transitions are atomic but locks are released before
# any outbound send so we never hold a lock across network I/O.
_group_locks: dict[int, asyncio.Lock] = {}


def _get_group_lock(group_id: int) -> asyncio.Lock:
    lock = _group_locks.get(group_id)
    if lock is None:
        lock = asyncio.Lock()
        _group_locks[group_id] = lock
    return lock


def _parse_text_only_number(message: Message) -> int | None:
    """Return a safe integer iff the message contains only numeric text.

    Rejects messages containing at, image, reply, or any other non-text segment.
    Rejects whitespace-padded text, signs, decimals, ordinary non-numeric text,
    and digit strings that exceed Python's safe integer-conversion limit.
    """
    parts: list[str] = []
    for segment in message:
        if segment.type != "text":
            return None
        parts.append(str(segment.data.get("text", "")))
    concatenated = "".join(parts)
    if not _NUMBER_RE.fullmatch(concatenated):
        return None
    try:
        return int(concatenated)
    except ValueError:
        return None


def _is_text_only_number(message: Message) -> bool:
    return _parse_text_only_number(message) is not None


async def _is_group_numeric(event: GroupMessageEvent) -> bool:
    return _is_text_only_number(event.message)


async def _react_success(bot: Bot, message_id: int) -> None:
    try:
        await bot.call_api(
            "set_msg_emoji_like",
            message_id=message_id,
            emoji_id=_SUCCESS_EMOJI_ID,
            set=True,
        )
    except Exception as exc:
        logger.warning("Failed to react to successful counter step: %r", exc)


matcher = on_message(
    rule=is_type(GroupMessageEvent) & Rule(_is_group_numeric),
    priority=20,
    block=True,
)


@matcher.handle()
async def handle_counter(bot: Bot, event: GroupMessageEvent) -> None:
    value = _parse_text_only_number(event.message)
    if value is None:
        return

    group_id = int(event.group_id)

    # Determine outcome inside a per-group lock, then release before sending.
    lock = _get_group_lock(group_id)
    async with lock:
        previous = _group_counters.get(group_id, 0)
        if value == previous + 1:
            _group_counters[group_id] = value
            success = True
            failure_message = None
        elif previous == 0:
            return
        else:
            success = False
            failure_message = (
                f"接龙失败！正确数字应为 {previous + 1}，"
                "已重置为 0，下一位请发 1。"
            )
            _group_counters[group_id] = 0

    if success:
        await _react_success(bot, int(event.message_id))
        return

    # Lock released; send the failure response.
    assert failure_message is not None
    await matcher.finish(failure_message)
