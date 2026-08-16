from __future__ import annotations

from collections import OrderedDict
from typing import Any

from nonebot import get_driver, get_plugin_config, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.plugin import PluginMetadata
from pydantic import BaseModel

GROUP_SUPERUSER_GATE_INTERFACE_VERSION = 2
_EVENT_CACHE_LIMIT = 1024
_event_access_cache: OrderedDict[tuple[str, int, int], bool] = OrderedDict()


class Config(BaseModel):
    group_superuser_gate_private_enabled: bool = True


__plugin_meta__ = PluginMetadata(
    name="group-superuser-gate",
    description="Shared same-group superuser access gate for internal plugins.",
    usage="Internal plugin.",
    type="library",
    config=Config,
    supported_adapters={"~onebot.v11"},
)
pconfig = get_plugin_config(Config)


@get_driver().on_startup
def log_gate_config() -> None:
    logger.info(
        "Group superuser gate configured: "
        f"private_enabled={pconfig.group_superuser_gate_private_enabled}"
    )


def _configured_superusers() -> set[str]:
    configured = getattr(get_driver().config, "superusers", set()) or set()
    return {str(user_id).strip() for user_id in configured if str(user_id).strip()}


def _event_cache_key(bot: Bot, event: GroupMessageEvent) -> tuple[str, int, int] | None:
    message_id = getattr(event, "message_id", None)
    if message_id is None:
        return None
    return str(bot.self_id), int(event.group_id), int(message_id)


def _cache_result(key: tuple[str, int, int] | None, allowed: bool) -> None:
    if key is None:
        return
    _event_access_cache[key] = allowed
    _event_access_cache.move_to_end(key)
    while len(_event_access_cache) > _EVENT_CACHE_LIMIT:
        _event_access_cache.popitem(last=False)


def _matches_superuser(bot: Bot, user_id: Any, superusers: set[str]) -> bool:
    normalized = str(user_id).strip()
    adapter_name = bot.adapter.get_name().split(maxsplit=1)[0].lower()
    return normalized in superusers or f"{adapter_name}:{normalized}" in superusers


async def _group_has_superuser(bot: Bot, event: GroupMessageEvent) -> bool:
    cache_key = _event_cache_key(bot, event)
    if cache_key in _event_access_cache:
        return _event_access_cache[cache_key]

    superusers = _configured_superusers()
    if not superusers:
        _cache_result(cache_key, False)
        return False

    try:
        members = await bot.get_group_member_list(group_id=event.group_id)
    except Exception:
        logger.warning(
            f"Failed to check superuser membership for group {event.group_id}",
            exc_info=True,
        )
        _cache_result(cache_key, False)
        return False

    allowed = any(
        _matches_superuser(bot, member["user_id"], superusers)
        for member in members
        if member.get("user_id") is not None
    )
    _cache_result(cache_key, allowed)
    return allowed


async def event_access_allowed(bot: Bot, event: Any) -> bool:
    if isinstance(event, PrivateMessageEvent):
        return pconfig.group_superuser_gate_private_enabled
    if isinstance(event, GroupMessageEvent):
        return await _group_has_superuser(bot, event)
    return False


__all__ = [
    "GROUP_SUPERUSER_GATE_INTERFACE_VERSION",
    "event_access_allowed",
]
