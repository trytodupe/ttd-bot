from nonebot import on_message, Bot
from nonebot.rule import Rule
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Event

from .face import emoji_like_id_set

import logging
import random

__plugin_meta__ = PluginMetadata(
    name="auto-react",
    description="",
    usage="",
)


async def target_user(event: Event):
    return (event.get_user_id() in ["1914166403"])


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
