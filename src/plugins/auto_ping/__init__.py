from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

from nonebot import (
    CommandGroup,
    get_driver,
    get_plugin_config,
    logger,
    on_message,
    on_notice,
    require,
)
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    NoticeEvent,
)
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot_plugin_uninfo import QryItrface, SceneType
from nonebot_plugin_uninfo import User

from .config import Config
from .emoji import PROPOSAL_EMOJI_IDS
from .helpers import (
    AddCommandArgs,
    format_alias_lines,
    parse_add_command_args,
    parse_proposal_add_command_args,
    parse_remove_command_args,
    pick_display_name,
    visible_targets,
)
from .proposals import (
    AliasProposal,
    ProposalStore,
    ReactionNotice,
    create_proposal,
    parse_reaction_notice,
)
from .storage import AliasConflictError, AliasNotFoundError, AliasRegistry


require("nonebot_plugin_localstore")
require("nonebot_plugin_uninfo")


__plugin_meta__ = PluginMetadata(
    name="auto-ping",
    description="Ping configured users when their aliases appear in group messages.",
    usage=(
        "ttd ping add <alias> @user\n"
        "ttd ping remove <alias>\n"
        "ttd ping list"
    ),
    config=Config,
)

config = get_plugin_config(Config)
driver = get_driver()
registry = AliasRegistry()
proposal_store = ProposalStore()
_REGISTRY_LOCK = asyncio.Lock()


async def _is_group(event: Event) -> bool:
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


def _is_superuser_id(bot: Bot, qq: int | str) -> bool:
    user_id = str(qq)
    adapter_name = bot.adapter.get_name().split(maxsplit=1)[0].lower()
    superusers = {str(item) for item in bot.config.superusers}
    return (
        user_id in superusers
        or f"{adapter_name}:{user_id}" in superusers
    )


def _can_remove_alias(bot: Bot, requester_qq: int | str, owner_qq: int) -> bool:
    return int(requester_qq) == owner_qq or _is_superuser_id(bot, requester_qq)


def _extract_message_id(result: Any) -> int:
    value = (
        result.get("message_id")
        if isinstance(result, dict)
        else getattr(result, "message_id", None)
    )
    if value is None:
        raise ValueError("send_group_msg did not return message_id")
    return int(value)


def _build_proposal_message(
    parsed: AddCommandArgs,
    approve_emoji_id: int,
    reject_emoji_id: int,
) -> Message:
    message = Message(f"Proposal: {parsed.alias} -> ")
    message += MessageSegment.at(parsed.target_qq)
    message += MessageSegment.text("\n赞成：")
    message += MessageSegment.face(approve_emoji_id)
    message += MessageSegment.text(" 反对：")
    message += MessageSegment.face(reject_emoji_id)
    return message


def _should_approve_reaction(bot: Bot, notice: ReactionNotice) -> bool:
    if not notice.is_add or notice.user_id == int(bot.self_id):
        return False
    approval_count = max(0, notice.count - 1)
    return (
        approval_count >= config.auto_ping_proposal_approval_threshold
        or _is_superuser_id(bot, notice.user_id)
    )


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


def _remove_pending_proposal(alias: str) -> None:
    pending = proposal_store.find_by_alias(alias)
    if pending is not None:
        proposal_store.remove(pending.group_id, pending.message_id)


async def _create_alias_proposal(
    bot: Bot,
    event: GroupMessageEvent,
    parsed: AddCommandArgs,
) -> None:
    approve_emoji_id, reject_emoji_id = random.sample(PROPOSAL_EMOJI_IDS, 2)
    proposal_message = _build_proposal_message(
        parsed,
        approve_emoji_id,
        reject_emoji_id,
    )

    async with _REGISTRY_LOCK:
        owner_qq = registry.get_alias_owner(parsed.alias)
        if owner_qq is not None:
            raise AliasConflictError(parsed.alias, owner_qq)
        if proposal_store.find_by_alias(parsed.alias) is not None:
            raise ValueError(f"Proposal already pending: {parsed.alias}")

        result = await bot.call_api(
            "send_group_msg",
            group_id=int(event.group_id),
            message=proposal_message,
        )
        message_id = _extract_message_id(result)
        proposal = create_proposal(
            message_id=message_id,
            group_id=int(event.group_id),
            proposer_qq=int(event.user_id),
            target_qq=parsed.target_qq,
            alias=parsed.alias,
            approve_emoji_id=str(approve_emoji_id),
            reject_emoji_id=str(reject_emoji_id),
        )
        proposal_store.add(proposal)

        try:
            for emoji_id in (approve_emoji_id, reject_emoji_id):
                await bot.call_api(
                    "set_msg_emoji_like",
                    message_id=message_id,
                    emoji_id=str(emoji_id),
                    set=True,
                )
        except Exception:
            proposal_store.remove(proposal.group_id, proposal.message_id)
            try:
                await bot.call_api("delete_msg", message_id=message_id)
            except Exception as cleanup_error:
                logger.warning("Failed to retract incomplete ping proposal: %r", cleanup_error)
            raise


async def _approve_proposal(bot: Bot, proposal: AliasProposal) -> bool:
    async with _REGISTRY_LOCK:
        current = proposal_store.get(proposal.group_id, proposal.message_id)
        if current != proposal:
            return False

        owner_qq = registry.get_alias_owner(proposal.alias)
        if owner_qq is not None:
            proposal_store.remove(proposal.group_id, proposal.message_id)
            return False

        try:
            registry.add_alias(proposal.target_qq, proposal.alias)
        except AliasConflictError:
            proposal_store.remove(proposal.group_id, proposal.message_id)
            return False
        proposal_store.remove(proposal.group_id, proposal.message_id)

    message = Message(f"Alias added: {proposal.alias} -> ")
    message += MessageSegment.at(proposal.target_qq)
    try:
        await bot.call_api(
            "send_group_msg",
            group_id=proposal.group_id,
            message=message,
        )
    except Exception as exc:
        logger.warning("Ping proposal was approved but notification failed: %r", exc)
    return True


async def _is_tracked_proposal_reaction(event: Event) -> bool:
    notice = parse_reaction_notice(event)
    if notice is None:
        return False
    proposal = proposal_store.get(notice.group_id, notice.message_id)
    return proposal is not None and notice.emoji_id in {
        proposal.approve_emoji_id,
        proposal.reject_emoji_id,
    }


reaction_matcher = on_notice(
    rule=Rule(_is_tracked_proposal_reaction),
    priority=1,
    block=False,
)


@matcher.handle()
async def handle(bot: Bot, event: GroupMessageEvent) -> None:
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


ping_cmd_group = CommandGroup("ping", priority=10, block=True)
ping_add_cmd = ping_cmd_group.command("add")
ping_remove_cmd = ping_cmd_group.command("remove")
ping_list_cmd = ping_cmd_group.command("list")


@ping_add_cmd.handle()
async def handle_ping_add(
    bot: Bot,
    event: MessageEvent,
    interface: QryItrface,
    args: Message = CommandArg(),
) -> None:
    is_superuser = _is_superuser_id(bot, event.user_id)

    if not is_superuser:
        if not isinstance(event, GroupMessageEvent):
            await ping_add_cmd.finish("Alias proposals are only available in group chats.")
        try:
            parsed = parse_proposal_add_command_args(args)
        except ValueError as exc:
            await ping_add_cmd.finish(str(exc))

        display_name = await _get_group_display_name(
            interface,
            int(event.group_id),
            parsed.target_qq,
        )
        if display_name is None:
            await ping_add_cmd.finish("Target user is not a member of this group.")

        try:
            await _create_alias_proposal(bot, event, parsed)
        except (AliasConflictError, ValueError) as exc:
            await ping_add_cmd.finish(str(exc))
        except Exception as exc:
            logger.exception("Failed to create ping proposal: %r", exc)
            await ping_add_cmd.finish("Failed to create alias proposal.")
        await ping_add_cmd.finish()

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
        _remove_pending_proposal(parsed.alias)

    await ping_add_cmd.finish(
        f"Alias added: {parsed.alias} -> {display_name} ({parsed.target_qq})"
    )


@ping_remove_cmd.handle()
async def handle_ping_remove(
    bot: Bot,
    event: MessageEvent,
    interface: QryItrface,
    args: Message = CommandArg(),
) -> None:
    try:
        alias = parse_remove_command_args(args)
    except ValueError as exc:
        await ping_remove_cmd.finish(str(exc))

    owner_qq = registry.get_alias_owner(alias)
    if owner_qq is not None and not _can_remove_alias(bot, event.user_id, owner_qq):
        await ping_remove_cmd.finish("You can only remove your own aliases.")

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
        current_owner_qq = registry.get_alias_owner(alias)
        if (
            current_owner_qq is not None
            and not _can_remove_alias(bot, event.user_id, current_owner_qq)
        ):
            await ping_remove_cmd.finish("You can only remove your own aliases.")
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


@reaction_matcher.handle()
async def handle_proposal_reaction(bot: Bot, event: NoticeEvent) -> None:
    notice = parse_reaction_notice(event)
    if notice is None:
        return

    proposal = proposal_store.get(notice.group_id, notice.message_id)
    if proposal is None or notice.emoji_id != proposal.approve_emoji_id:
        return
    if _should_approve_reaction(bot, notice):
        await _approve_proposal(bot, proposal)


async def _fetch_approval_voters(bot: Bot, proposal: AliasProposal) -> set[int]:
    result = await bot.call_api(
        "get_emoji_likes",
        message_id=proposal.message_id,
        emoji_id=proposal.approve_emoji_id,
    )
    if not isinstance(result, dict):
        return set()
    items = result.get("emoji_like_list", [])
    if not isinstance(items, list):
        return set()

    voters: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            voters.add(int(item.get("user_id")))
        except (TypeError, ValueError):
            continue
    voters.discard(int(bot.self_id))
    return voters


@driver.on_bot_connect
async def _recover_pending_proposals(bot: Bot) -> None:
    for proposal in proposal_store.all():
        if registry.get_alias_owner(proposal.alias) is not None:
            async with _REGISTRY_LOCK:
                proposal_store.remove(proposal.group_id, proposal.message_id)
            continue

        try:
            voters = await _fetch_approval_voters(bot, proposal)
        except Exception as exc:
            logger.warning(
                "Failed to recover ping proposal %s/%s: %r",
                proposal.group_id,
                proposal.message_id,
                exc,
            )
            continue

        if (
            len(voters) >= config.auto_ping_proposal_approval_threshold
            or any(_is_superuser_id(bot, voter) for voter in voters)
        ):
            await _approve_proposal(bot, proposal)
