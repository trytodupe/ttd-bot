from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from nonebot.adapters.onebot.v11 import Message
from nonebot_plugin_uninfo import Member, User

from .storage import normalize_alias


ADD_USAGE = "Usage: ttd ping add <qq|@user> <alias>"
REMOVE_USAGE = "Usage: ttd ping remove <alias>"


@dataclass(frozen=True)
class AddCommandArgs:
    target_qq: int
    alias: str


def _extract_at_targets(args: Message) -> list[str]:
    targets: list[str] = []
    for seg in args:
        if seg.type != "at":
            continue
        qq = seg.data.get("qq")
        if qq is None:
            continue
        targets.append(str(qq))
    return targets


def _extract_tokens(args: Message) -> list[str]:
    text = " ".join(seg.data.get("text", "") for seg in args if seg.type == "text")
    return text.strip().split()


def parse_add_command_args(args: Message, *, is_group: bool) -> AddCommandArgs:
    at_targets = _extract_at_targets(args)
    tokens = _extract_tokens(args)

    if len(at_targets) > 1:
        raise ValueError("Only one @user is allowed.")

    if at_targets:
        if not is_group:
            raise ValueError("Private chat does not support @user. Use a QQ number instead.")
        if len(tokens) != 1:
            raise ValueError(ADD_USAGE)
        if not at_targets[0].isdigit():
            raise ValueError("Only one numeric @user is allowed.")
        return AddCommandArgs(target_qq=int(at_targets[0]), alias=normalize_alias(tokens[0]))

    if len(tokens) != 2:
        raise ValueError(ADD_USAGE)

    qq_text, alias_text = tokens
    if not qq_text.isdigit():
        raise ValueError("QQ should contain digits only.")

    return AddCommandArgs(target_qq=int(qq_text), alias=normalize_alias(alias_text))


def parse_remove_command_args(args: Message) -> str:
    if _extract_at_targets(args):
        raise ValueError(REMOVE_USAGE)

    tokens = _extract_tokens(args)
    if len(tokens) != 1:
        raise ValueError(REMOVE_USAGE)
    return normalize_alias(tokens[0])


def pick_display_name(*, qq: int | str, member: Member | None = None, user: User | None = None) -> str:
    if member and member.nick:
        return member.nick
    if member and member.user.name:
        return member.user.name
    if user and user.name:
        return user.name
    return str(qq)


def visible_targets(
    targets: Mapping[int, tuple[str, ...]],
    visible_qqs: Iterable[int],
) -> list[tuple[int, tuple[str, ...]]]:
    visible = set(int(qq) for qq in visible_qqs)
    return [
        (qq, aliases)
        for qq, aliases in sorted(targets.items())
        if qq in visible
    ]


def format_alias_lines(entries: list[tuple[str, int, tuple[str, ...]]]) -> str:
    return "\n".join(
        f"{display_name} ({qq}): {', '.join(aliases)}"
        for display_name, qq, aliases in entries
    )
