from nonebot import Bot, on_message, on_notice
from nonebot.adapters.onebot.v11 import GroupBanNoticeEvent, Message, MessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, is_type, to_me

__plugin_meta__ = PluginMetadata(
    name="easy-trigger",
    description="Lightweight reactive triggers for mute notices and superuser pings.",
    usage="",
)

MUTE_REPLY = "还能说话吗？"
SELF_UNMUTED_REPLY = "哥我知道错了"
SUPERUSER_PING_REPLY = "我错了"
_SIMPLE_PING_SEGMENT_TYPES = {"text", "at"}
_SUPERUSER_PING_KEYWORD = "ttd"

mute_handler = on_notice(rule=is_type(GroupBanNoticeEvent), priority=1, block=False)


def _is_simple_ping(message: Message) -> bool:
    for segment in message:
        if getattr(segment, "type", None) not in _SIMPLE_PING_SEGMENT_TYPES:
            return False
    return not message.extract_plain_text().strip()


def _contains_superuser_ping_keyword(message: Message) -> bool:
    return _SUPERUSER_PING_KEYWORD in message.extract_plain_text().lower()


def _should_handle_superuser_ping(event: MessageEvent) -> bool:
    return _is_simple_ping(event.message) or _contains_superuser_ping_keyword(event.message)


superuser_ping_handler = on_message(
    rule=to_me() & Rule(_should_handle_superuser_ping),
    permission=SUPERUSER,
    priority=1,
    block=False,
)


@mute_handler.handle()
async def handle_mute(event: GroupBanNoticeEvent, bot: Bot):
    self_id = int(bot.self_id)

    if (event.duration > 0) and (event.user_id != self_id):
        await mute_handler.finish(at_sender=event.user_id, message=MUTE_REPLY)

    if (event.duration == 0) and (event.user_id == self_id):
        await mute_handler.finish(message=SELF_UNMUTED_REPLY)


@superuser_ping_handler.handle()
async def handle_superuser_ping() -> None:
    await superuser_ping_handler.finish(message=SUPERUSER_PING_REPLY)
