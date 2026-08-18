"""
Typhoon query plugin for ttd-bot.
Query current typhoon positions and forecasts from CMA data.
"""

from __future__ import annotations

from nonebot import CommandGroup, get_plugin_config
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import is_type, to_me

from .api import get_typhoon_list
from .config import Config
from .formatter import format_typhoon_full, format_typhoon_list

__plugin_meta__ = PluginMetadata(
    name="typhoon-query",
    description="查询当前西太平洋台风位置和路径预报",
    usage="ttd typhoon [编号]",
    config=Config,
    homepage="https://github.com/trytodupe/ttd-bot",
    supported_adapters={"~onebot.v11"},
)

plugin_config = get_plugin_config(Config)

# Command rule for group messages
command_rule = is_type(GroupMessageEvent) & to_me()

# Command group
typhoon_cmd = CommandGroup("typhoon", rule=command_rule, priority=10, block=True)

# Main command: ttd typhoon [typhoon_id]
typhoon_main = typhoon_cmd.command("")


@typhoon_main.handle()
async def handle_typhoon(event: GroupMessageEvent, args: Message = CommandArg()):
    """Handle typhoon query command."""
    arg_text = args.extract_plain_text().strip()
    
    # Get typhoon list
    typhoons = await get_typhoon_list(timeout=plugin_config.typhoon_api_timeout)
    
    if not typhoons:
        await typhoon_main.finish("🌀 当前西太平洋无活跃台风")
    
    # If specific typhoon ID requested
    if arg_text:
        # Try to find typhoon by ID
        target = None
        for t in typhoons:
            if t.tfid == arg_text or t.name == arg_text or t.ename.lower() == arg_text.lower():
                target = t
                break
        
        if target:
            message = format_typhoon_full(target)
            await typhoon_main.finish(message)
        else:
            available = ", ".join(f"{t.name}({t.tfid})" for t in typhoons)
            await typhoon_main.finish(
                f"未找到台风 {arg_text}\n"
                f"当前活跃台风：{available}"
            )
    
    # Show all typhoons
    if len(typhoons) == 1:
        # Only one typhoon, show details
        message = format_typhoon_full(typhoons[0])
    else:
        # Multiple typhoons, show list
        message = format_typhoon_list(typhoons)
    
    await typhoon_main.finish(message)
