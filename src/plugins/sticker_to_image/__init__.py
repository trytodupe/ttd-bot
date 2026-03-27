from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

__plugin_meta__ = PluginMetadata(
    name="sticker-to-image",
    description="Reply to stickers with a normal image in private chats and group to_me messages.",
    usage="Send a sticker image in private chat, or @ the bot with one in group chat.",
)


def _should_handle_event(event: MessageEvent) -> bool:
    message_type = str(getattr(event, "message_type", "")).strip().lower()
    if message_type == "private":
        return True
    if message_type == "group":
        return bool(getattr(event, "to_me", False))
    return False


def _extract_sticker_source(message: Message) -> str | None:
    for segment in message:
        if getattr(segment, "type", None) != "image":
            continue

        data = dict(getattr(segment, "data", {}) or {})
        sub_type = str(data.get("sub_type") or data.get("subtype") or "").strip()
        if sub_type != "1":
            continue

        for key in ("url", "file"):
            value = str(data.get(key, "")).strip()
            if value:
                return value
    return None


def _build_image_reply(source: str) -> MessageSegment:
    return MessageSegment.image(source)


matcher = on_message(rule=Rule(_should_handle_event), priority=50, block=False)


@matcher.handle()
async def handle_sticker(event: MessageEvent) -> None:
    source = _extract_sticker_source(event.message)
    if not source:
        return

    await matcher.finish(_build_image_reply(source))
