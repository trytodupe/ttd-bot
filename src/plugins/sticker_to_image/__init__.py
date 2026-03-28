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


def _extract_control_text(message: Message) -> str:
    text_parts: list[str] = []
    for segment in message:
        if getattr(segment, "type", None) != "text":
            continue
        text_parts.append(str(getattr(segment, "data", {}).get("text", "")))
    return "".join(text_parts).strip().lower()


def _extract_reply_sticker_source(event: MessageEvent) -> str | None:
    if not bool(getattr(event, "to_me", False)):
        return None

    reply = getattr(event, "reply", None)
    if reply is None:
        return None

    control_text = _extract_control_text(event.message)
    if control_text not in {"", "url"}:
        return None

    reply_message = getattr(reply, "message", None)
    if not isinstance(reply_message, Message):
        return None
    return _extract_sticker_source(reply_message)


def _build_image_reply(source: str) -> MessageSegment:
    return MessageSegment.image(source)


matcher = on_message(rule=Rule(_should_handle_event), priority=50, block=False)


@matcher.handle()
async def handle_sticker(event: MessageEvent) -> None:
    source = _extract_sticker_source(event.message)
    if source:
        return await matcher.finish(_build_image_reply(source))

    reply_source = _extract_reply_sticker_source(event)
    if not reply_source:
        return

    if _extract_control_text(event.message) == "url":
        return await matcher.finish(reply_source)

    return await matcher.finish(_build_image_reply(reply_source))
