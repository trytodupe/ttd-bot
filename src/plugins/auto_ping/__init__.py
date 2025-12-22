from __future__ import annotations

import time
from dataclasses import dataclass

from nonebot import get_plugin_config, on_message
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment

from .config import Config


__plugin_meta__ = PluginMetadata(
    name="auto-ping",
    description="Ping configured users when their aliases appear in group messages.",
    usage="Configure via auto_ping_alias_map / auto_ping_targets in .env",
    config=Config,
)

config = get_plugin_config(Config)


def _build_alias_map(cfg: Config) -> dict[str, int]:
    alias_map: dict[str, int] = {}
    for alias, qq in (cfg.auto_ping_alias_map or {}).items():
        if not alias:
            continue
        alias_map[str(alias).casefold()] = int(qq)
    for target in cfg.auto_ping_targets or []:
        qq = int(target.qq)
        for alias in target.aliases:
            if not alias:
                continue
            alias_map[str(alias).casefold()] = qq
    return alias_map


ALIAS_MAP = _build_alias_map(config)


async def _is_group(event) -> bool:
    return isinstance(event, GroupMessageEvent)


matcher = on_message(rule=Rule(_is_group), priority=50, block=False)


@dataclass(frozen=True)
class _MemberCacheEntry:
    expires_at: float
    member_ids: set[int]


_member_cache: dict[int, _MemberCacheEntry] = {}
_MEMBER_CACHE_TTL_SECONDS = 3600


async def _get_group_member_ids(bot: Bot, group_id: int) -> set[int]:
    now = time.monotonic()
    cached = _member_cache.get(group_id)
    if cached and cached.expires_at > now:
        return cached.member_ids

    members = await bot.call_api("get_group_member_list", group_id=group_id)
    member_ids = {int(m["user_id"]) for m in members}
    _member_cache[group_id] = _MemberCacheEntry(
        expires_at=now + _MEMBER_CACHE_TTL_SECONDS,
        member_ids=member_ids,
    )
    return member_ids


def _match_targets(plain_text: str) -> set[int]:
    if not plain_text or not ALIAS_MAP:
        return set()

    normalized = plain_text.casefold()
    targets: set[int] = set()
    for alias, qq in ALIAS_MAP.items():
        if alias and (alias in normalized):
            targets.add(int(qq))
    return targets


@matcher.handle()
async def handle(bot: Bot, event: GroupMessageEvent):
    targets = _match_targets(event.get_plaintext())
    if not targets:
        return

    member_ids = await _get_group_member_ids(bot, int(event.group_id))
    targets_in_group = [qq for qq in sorted(targets) if qq in member_ids]
    if not targets_in_group:
        return

    msg = Message()
    for qq in targets_in_group:
        msg += MessageSegment.at(qq)
        msg += MessageSegment.text(" ")

    await matcher.send(msg)
