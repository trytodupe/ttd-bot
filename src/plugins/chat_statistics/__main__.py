import re
from nonebot import on_command
from nonebot.rule import is_type, to_me
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.exception import FinishedException

from .statistics import get_user_chat_statistics, get_user_active_statistics
from .visualization import generate_combined_chart


def extract_at_users(raw_message: str) -> list[str]:
    """Extract user IDs from CQ at codes in raw message"""
    pattern = r'\[CQ:at,qq=(\d+)(?:,.*?)?\]'
    return re.findall(pattern, raw_message)


# Create command handler
command_rule = is_type(GroupMessageEvent) & to_me()
chat_cmd = on_command("chat", rule=command_rule, priority=10, block=True)


@chat_cmd.handle()
async def handle_chat_statistics(event: GroupMessageEvent, args: Message = CommandArg()):
    """Handle combined statistics command with image generation"""
    
    # Parse the days argument
    try:
        arg_text = args.extract_plain_text().strip()
        if not arg_text:
            days = 7  # Default to 7 days
        else:
            days = int(arg_text)
            if days <= 0:
                await chat_cmd.finish("天数必须大于0")
                return
    except ValueError:
        await chat_cmd.finish("请输入有效的天数")
        return
    
    group_id = str(event.group_id)
    
    # Check if message contains @ mentions
    at_users = extract_at_users(event.raw_message)
    if at_users:
        # If there are @ mentions, get statistics for the first mentioned user
        target_user_id = at_users[0]
        user_display = f"用户 {target_user_id}"
    else:
        # Otherwise, get statistics for the requesting user
        target_user_id = str(event.user_id)
        user_display = "您"
    
    try:
        # Get both statistics for the target user
        chat_stats = await get_user_chat_statistics(target_user_id, group_id, days)
        active_stats = await get_user_active_statistics(target_user_id, group_id, days)
        
        # Try to generate combined chart image
        chart_bytes = await generate_combined_chart(chat_stats, active_stats, user_display)
        
        if chart_bytes is None:
            # Fallback to text if image generation failed
            chat_text = chat_stats.format_text_output(user_display)
            active_text = active_stats.format_text_output(user_display)
            await chat_cmd.finish(f"{chat_text}\n\n{active_text}")
        else:
            # Send image
            image_segment = MessageSegment.image(chart_bytes)
            await chat_cmd.finish(image_segment)
        
    except FinishedException:
        # Re-raise FinishedException as it's the normal way to end command processing
        raise
    except Exception as e:
        await chat_cmd.finish(f"获取统计数据时出错: {str(e)}")

