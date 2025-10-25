import logging
import random
from pathlib import Path

from nonebot import on_message, Bot, require, CommandGroup
from nonebot.rule import Rule, to_me
from nonebot.plugin import PluginMetadata
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Event,
    MessageEvent,
    Message,
)
from nonebot.permission import SUPERUSER
import nonebot_plugin_localstore as store

from .face import emoji_like_id_set
from .user_storage import UserStorage

require("nonebot_plugin_localstore")

DATA_FILE: Path = store.get_data_file(plugin_name="nonebot_plugin_auto_react", filename="target_users.json")
user_storage = UserStorage(DATA_FILE)


__plugin_meta__ = PluginMetadata(
    name="auto-react",
    description="",
    usage="",
)


async def target_user(event: Event):
    return user_storage.has_user(event.get_user_id())


matcher = on_message(
    rule=Rule(target_user),
    priority=1000,
    block=False,
)

@matcher.handle()
async def handle(bot: Bot, event: GroupMessageEvent):
    try:
        emoji_id = random.choice(list(emoji_like_id_set))

        await bot.call_api(
            "set_msg_emoji_like",  # OneBot协议标准API
            message_id=event.message_id,
            emoji_id=emoji_id,  # 目标表情ID
        )
        
    except Exception as e:
        logging.error(f"Failed to add emoji reaction: {e}")



react_cmd_group = CommandGroup("react", rule=to_me(), permission=SUPERUSER, priority=10, block=True)
add_cmd = react_cmd_group.command("add ")
remove_cmd = react_cmd_group.command("remove ")

@add_cmd.handle()
async def handle_add(event: MessageEvent, args: Message = CommandArg()):
    user_id = args.extract_plain_text().split()[0]

    if not user_id.isdigit():
        await add_cmd.finish("[!] 用户ID 应该只包含数字")
    
    if user_storage.add_user(user_id):
        await add_cmd.finish(f"[+] 已添加用户 {user_id}")
    else:
        await add_cmd.finish(f"[!] 用户 {user_id} 已存在")

@remove_cmd.handle()
async def handle_remove(event: MessageEvent, args: Message = CommandArg()):
    user_id = args.extract_plain_text().split()[0]

    if not user_id.isdigit():
        await remove_cmd.finish("[!] 用户ID 应该只包含数字")
    
    if user_storage.remove_user(user_id):
        await remove_cmd.finish(f"[-] 已移除用户 {user_id}")
    else:
        await remove_cmd.finish(f"[!] 用户 {user_id} 不存在")
