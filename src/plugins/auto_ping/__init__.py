from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from nonebot import CommandGroup, on_message, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot_plugin_uninfo import QryItrface, SceneType
from nonebot_plugin_uninfo import User

from .helpers import (
    format_alias_lines,
    parse_add_command_args,
    parse_remove_command_args,
    pick_display_name,
    visible_targets,
)
from .storage import AliasConflictError, AliasNotFoundError, AliasRegistry


require("nonebot_plugin_localstore")
require("nonebot_plugin_uninfo")


__plugin_meta__ = PluginMetadata(
    name="auto-ping",
    description="Ping configured users when their aliases appear in group messages.",
    usage="ttd ping add <qq|@user> <alias>\nttd ping remove <alias>\nttd ping list",
)

registry = AliasRegistry()
_REGISTRY_LOCK = asyncio.Lock()


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
    return registry.match_targets(plain_text)


async def _get_group_display_name(interface: QryItrface, group_id: int, qq: int) -> str | None:
    member = await interface.get_member(SceneType.GROUP, str(group_id), str(qq))
    if member is None:
        return None
    return pick_display_name(member=member, qq=qq)


async def _get_private_display_name(interface: QryItrface, qq: int) -> str:
    user = await interface.get_user(str(qq))
    return pick_display_name(user=user, qq=qq)


async def _list_group_aliases(interface: QryItrface, group_id: int) -> str:
    members = await interface.get_members(SceneType.GROUP, str(group_id))
    member_by_qq = {
        int(member.user.id): member
        for member in members
        if member.user.id.isdigit()
    }
    entries = []
    for qq, aliases in visible_targets(registry.all_targets(), member_by_qq.keys()):
        entries.append((pick_display_name(member=member_by_qq[qq], qq=qq), qq, aliases))
    if not entries:
        return "No aliases configured for members in this group."
    return "Configured aliases:\n" + format_alias_lines(entries)


async def _list_all_aliases(interface: QryItrface) -> str:
    entries: list[tuple[str, int, tuple[str, ...]]] = []
    for qq, aliases in registry.iter_targets():
        user: User | None = await interface.get_user(str(qq))
        entries.append((pick_display_name(user=user, qq=qq), qq, aliases))
    if not entries:
        return "No aliases configured."
    return "Configured aliases:\n" + format_alias_lines(entries)


ping_cmd_group = CommandGroup("ping", permission=SUPERUSER, priority=10, block=True)
ping_add_cmd = ping_cmd_group.command("add")
ping_remove_cmd = ping_cmd_group.command("remove")
ping_list_cmd = ping_cmd_group.command("list")


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


@ping_add_cmd.handle()
async def handle_ping_add(
    event: MessageEvent,
    interface: QryItrface,
    args: Message = CommandArg(),
) -> None:
    try:
        parsed = parse_add_command_args(args, is_group=isinstance(event, GroupMessageEvent))
    except ValueError as exc:
        await ping_add_cmd.finish(str(exc))

    if isinstance(event, GroupMessageEvent):
        display_name = await _get_group_display_name(interface, int(event.group_id), parsed.target_qq)
        if display_name is None:
            await ping_add_cmd.finish("Target user is not a member of this group.")
    else:
        display_name = await _get_private_display_name(interface, parsed.target_qq)

    async with _REGISTRY_LOCK:
        try:
            registry.add_alias(parsed.target_qq, parsed.alias)
        except AliasConflictError:
            await ping_add_cmd.finish(f"Alias already in use: {parsed.alias}")

    await ping_add_cmd.finish(
        f"Alias added: {parsed.alias} -> {display_name} ({parsed.target_qq})"
    )


@ping_remove_cmd.handle()
async def handle_ping_remove(
    event: MessageEvent,
    interface: QryItrface,
    args: Message = CommandArg(),
) -> None:
    try:
        alias = parse_remove_command_args(args)
    except ValueError as exc:
        await ping_remove_cmd.finish(str(exc))

    owner_qq = registry.get_alias_owner(alias)
    if isinstance(event, GroupMessageEvent):
        if owner_qq is None:
            await ping_remove_cmd.finish("Alias not found in this group.")
        display_name = await _get_group_display_name(interface, int(event.group_id), owner_qq)
        if display_name is None:
            await ping_remove_cmd.finish("Alias not found in this group.")
    else:
        if owner_qq is None:
            await ping_remove_cmd.finish(f"Alias not found: {alias}")
        display_name = await _get_private_display_name(interface, owner_qq)

    async with _REGISTRY_LOCK:
        try:
            removed_qq = registry.remove_alias(alias)
        except AliasNotFoundError:
            if isinstance(event, GroupMessageEvent):
                await ping_remove_cmd.finish("Alias not found in this group.")
            await ping_remove_cmd.finish(f"Alias not found: {alias}")

    await ping_remove_cmd.finish(
        f"Alias removed: {alias} from {display_name} ({removed_qq})"
    )


@ping_list_cmd.handle()
async def handle_ping_list(event: MessageEvent, interface: QryItrface) -> None:
    if isinstance(event, GroupMessageEvent):
        await ping_list_cmd.finish(await _list_group_aliases(interface, int(event.group_id)))
    await ping_list_cmd.finish(await _list_all_aliases(interface))
