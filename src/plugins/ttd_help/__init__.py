from __future__ import annotations

from nonebot import Bot, CommandGroup, logger
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .formatter import format_doc_detail, format_help_index
from .registry import get_feature_doc

__plugin_meta__ = PluginMetadata(
    name="ttd-help",
    description="Chinese help index for ttd-bot features.",
    usage="ttd help\nttd help <功能名>",
)

help_cmd_group = CommandGroup("help", priority=10, block=True)
help_cmd = help_cmd_group.command(tuple())
help_admin_cmd = help_cmd_group.command("admin", permission=SUPERUSER)
help_auto_cmd = help_cmd_group.command("auto")


def _is_long_message(text: str) -> bool:
    return len(text) > 1200 or text.count("\n") > 35


async def _finish_help_text(bot: Bot, event: MessageEvent, text: str) -> None:
    if not _is_long_message(text) or not isinstance(event, GroupMessageEvent):
        await help_cmd.finish(text)

    nodes = [
        {
            "type": "node",
            "data": {
                "name": "ttd help",
                "uin": str(bot.self_id),
                "content": text,
            },
        }
    ]
    try:
        await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
    except Exception as exc:
        logger.warning("Failed to send ttd help as group forward message: %r", exc)
        await help_cmd.finish(text)
    await help_cmd.finish()


async def _finish_help_for_args(
    bot: Bot,
    event: MessageEvent,
    args: Message,
    *,
    section: str = "public",
) -> None:
    query = args.extract_plain_text().strip()
    if not query:
        await _finish_help_text(bot, event, format_help_index(section=section))

    doc = get_feature_doc(query)
    if doc is None:
        await _finish_help_text(bot, event, f"没有找到功能：{query}\n发送 ttd help 查看功能列表。")

    await _finish_help_text(bot, event, format_doc_detail(doc))


@help_cmd.handle()
async def handle_help(bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _finish_help_for_args(bot, event, args)


@help_admin_cmd.handle()
async def handle_help_admin(bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _finish_help_for_args(bot, event, args, section="admin")


@help_auto_cmd.handle()
async def handle_help_auto(bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _finish_help_for_args(bot, event, args, section="background")
