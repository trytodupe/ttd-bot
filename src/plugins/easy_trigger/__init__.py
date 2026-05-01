from nonebot import Bot, get_plugin_config, on_message, on_notice
from nonebot.adapters.onebot.v11 import GroupBanNoticeEvent, Message, MessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, is_type, to_me

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="easy-trigger",
    description="Lightweight reactive triggers for mute notices and superuser pings.",
    usage="",
    config=Config,
)

TRIGGER_MUTE_NOTICE = "mute_notice"
TRIGGER_SUPERUSER_PING = "superuser_ping"
MUTE_REPLY = "还能说话吗？"
SELF_UNMUTED_REPLY = "哥我知道错了"
SUPERUSER_PING_REPLY = "我错了"
_SIMPLE_PING_SEGMENT_TYPES = {"text", "at"}
_SUPERUSER_PING_KEYWORD = "ttd"

plugin_config = get_plugin_config(Config)


def _contains(values: dict[str, set[str]], trigger_name: str, value: object) -> bool:
    return str(value) in values.get(trigger_name, set())


def _get_event_group_id(event: object) -> object | None:
    return getattr(event, "group_id", None)


def _is_trigger_allowed(trigger_name: str, event: object) -> bool:
    user_id = getattr(event, "user_id", None)
    group_id = _get_event_group_id(event)

    if user_id is not None and _contains(plugin_config.easy_trigger_user_whitelist, trigger_name, user_id):
        return True
    if group_id is not None and _contains(plugin_config.easy_trigger_group_whitelist, trigger_name, group_id):
        return True
    if user_id is not None and _contains(plugin_config.easy_trigger_user_blacklist, trigger_name, user_id):
        return False
    if group_id is not None and _contains(plugin_config.easy_trigger_group_blacklist, trigger_name, group_id):
        return False
    return True


def _should_handle_mute_notice(event: GroupBanNoticeEvent) -> bool:
    return _is_trigger_allowed(TRIGGER_MUTE_NOTICE, event)


mute_handler = on_notice(rule=is_type(GroupBanNoticeEvent) & Rule(_should_handle_mute_notice), priority=1, block=False)


def _is_simple_ping(message: Message) -> bool:
    for segment in message:
        if getattr(segment, "type", None) not in _SIMPLE_PING_SEGMENT_TYPES:
            return False
    return not message.extract_plain_text().strip()


def _contains_superuser_ping_keyword(message: Message) -> bool:
    return _SUPERUSER_PING_KEYWORD in message.extract_plain_text().lower()


def _should_handle_superuser_ping(event: MessageEvent) -> bool:
    if not _is_trigger_allowed(TRIGGER_SUPERUSER_PING, event):
        return False
    if getattr(event, "reply", None) is not None:
        return False
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
