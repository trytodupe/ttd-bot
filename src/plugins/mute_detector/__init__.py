from nonebot import on_notice, Bot
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import GroupBanNoticeEvent

__plugin_meta__ = PluginMetadata(
    name="mute-detector",
    description="",
    usage="",
)

mute_handler = on_notice(priority=1, block=False)

@mute_handler.handle()
async def handle_mute(event: GroupBanNoticeEvent, bot: Bot):
    self_id = int(bot.self_id)

    # logger.info(f"self_id: {self_id};\n"
    #             f"user_id: {event.user_id};\n"
    #             f"operator_id: {event.operator_id};\n"
    #             f"duration: {event.duration};\n"
    #             f"group_id: {event.group_id};\n"
    #             )
    
    if (event.duration > 0) and (event.user_id != self_id):
        await mute_handler.finish(
            at_sender=event.user_id, 
            message="还能说话吗？")

    if (event.duration == 0) and (event.user_id == self_id):
        await mute_handler.finish(
            message="b")
