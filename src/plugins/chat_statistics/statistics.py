import datetime
from collections import defaultdict
from typing import Dict

from nonebot import require

require("nonebot_plugin_chatrecorder")
require("nonebot_plugin_uninfo")

from nonebot_plugin_chatrecorder import get_message_records


class ChatStatistics:
    """Chat statistics data structure for visualization interface"""
    
    def __init__(self, user_id: str, group_id: str, days: int):
        self.user_id = user_id
        self.group_id = group_id
        self.days = days
        self.hourly_distribution: Dict[int, int] = defaultdict(int)
        self.total_messages = 0
        self.start_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        self.end_time = datetime.datetime.now(datetime.timezone.utc)
    
    def add_message(self, message_time: datetime.datetime):
        """Add a message to the statistics"""
        # Convert UTC time to local time (UTC+8)
        if message_time.tzinfo is None:
            # If naive datetime, assume it's UTC
            message_time = message_time.replace(tzinfo=datetime.timezone.utc)
        
        # Convert to UTC+8 (China Standard Time)
        local_tz = datetime.timezone(datetime.timedelta(hours=8))
        local_time = message_time.astimezone(local_tz)
        hour = local_time.hour
        
        self.hourly_distribution[hour] += 1
        self.total_messages += 1
    
    def get_hourly_percentages(self) -> Dict[int, float]:
        """Get hourly distribution as percentages"""
        if self.total_messages == 0:
            return {}
        return {
            hour: (count / self.total_messages * 100)
            for hour, count in self.hourly_distribution.items()
        }
    
    def format_text_output(self, user_display: str = None) -> str:
        """Format statistics as text for display"""
        if self.total_messages == 0:
            user_part = f"{user_display}的" if user_display else ""
            return f"{user_part}过去{self.days}天无聊天记录"
        
        percentages = self.get_hourly_percentages()
        lines = []
        
        for hour in range(24):
            count = self.hourly_distribution.get(hour, 0)
            percentage = percentages.get(hour, 0)
            lines.append(f"{hour:2d}h: {count:3d} ({percentage:4.1f}%)")
        
        user_part = f"{user_display}的" if user_display else ""
        header = f"{user_part}过去{self.days}天聊天分布 (共{self.total_messages}条消息)"
        return f"{header}\n" + "\n".join(lines)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "user_id": self.user_id,
            "group_id": self.group_id,
            "days": self.days,
            "total_messages": self.total_messages,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "hourly_distribution": dict(self.hourly_distribution),
            "hourly_percentages": self.get_hourly_percentages()
        }


class ActiveStatistics:
    """Active time statistics - tracks which hours user was active (binary presence)"""
    
    def __init__(self, user_id: str, group_id: str, days: int):
        self.user_id = user_id
        self.group_id = group_id
        self.days = days
        # Track active days for each hour: hour -> set of active dates
        self.hourly_active_days: Dict[int, set] = defaultdict(set)
        self.start_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        self.end_time = datetime.datetime.now(datetime.timezone.utc)
    
    def add_message(self, message_time: datetime.datetime):
        """Add a message to the statistics (marks the hour as active for that date)"""
        # Convert UTC time to local time (UTC+8)
        if message_time.tzinfo is None:
            # If naive datetime, assume it's UTC
            message_time = message_time.replace(tzinfo=datetime.timezone.utc)
        
        # Convert to UTC+8 (China Standard Time)
        local_tz = datetime.timezone(datetime.timedelta(hours=8))
        local_time = message_time.astimezone(local_tz)
        hour = local_time.hour
        
        # Use date only (ignore time) to track unique active days
        date = local_time.date()
        self.hourly_active_days[hour].add(date)
    
    def get_hourly_active_counts(self) -> Dict[int, int]:
        """Get count of active days for each hour"""
        return {hour: len(days) for hour, days in self.hourly_active_days.items()}
    
    def get_hourly_percentages(self) -> Dict[int, float]:
        """Get hourly activity as percentages of total days"""
        if self.days == 0:
            return {}
        return {
            hour: (len(days) / self.days * 100)
            for hour, days in self.hourly_active_days.items()
        }
    
    def format_text_output(self, user_display: str = None) -> str:
        """Format statistics as text for display"""
        active_counts = self.get_hourly_active_counts()
        total_active_hours = len([h for h, count in active_counts.items() if count > 0])
        
        if total_active_hours == 0:
            user_part = f"{user_display}的" if user_display else ""
            return f"{user_part}过去{self.days}天无活跃记录"
        
        percentages = self.get_hourly_percentages()
        lines = []
        
        for hour in range(24):
            count = active_counts.get(hour, 0)
            percentage = percentages.get(hour, 0)
            lines.append(f"{hour:2d}h: {count:3d} ({percentage:5.1f}%)")
        
        user_part = f"{user_display}的" if user_display else ""
        header = f"{user_part}过去{self.days}天活跃时间分布 (共{total_active_hours}个活跃小时)"
        return f"{header}\n" + "\n".join(lines)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "user_id": self.user_id,
            "group_id": self.group_id,
            "days": self.days,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "hourly_active_counts": self.get_hourly_active_counts(),
            "hourly_percentages": self.get_hourly_percentages(),
            "total_active_hours": len([h for h, c in self.get_hourly_active_counts().items() if c > 0])
        }


async def get_user_chat_statistics(user_id: str, group_id: str, days: int) -> ChatStatistics:
    """Get chat statistics for a user in a specific group over the past N days"""
    
    stats = ChatStatistics(user_id, group_id, days)
    
    # Calculate time range - use UTC as required by chatrecorder
    end_time = datetime.datetime.now(datetime.timezone.utc)
    start_time = end_time - datetime.timedelta(days=days)
    
    # Get message records using the chatrecorder interface
    records = await get_message_records(
        user_ids=[user_id],
        scene_ids=[group_id],
        time_start=start_time,
        time_stop=end_time,
        types=["message"]
    )
    
    # Process the messages to build statistics
    for record in records:
        stats.add_message(record.time)
    
    return stats


async def get_group_chat_statistics(group_id: str, days: int) -> Dict[str, ChatStatistics]:
    """Get chat statistics for all users in a group over the past N days"""
    
    # Calculate time range - use UTC as required by chatrecorder
    end_time = datetime.datetime.now(datetime.timezone.utc)
    start_time = end_time - datetime.timedelta(days=days)
    
    user_stats: Dict[str, ChatStatistics] = {}
    
    # Get all message records for the group in the time range
    records = await get_message_records(
        scene_ids=[group_id],
        time_start=start_time,
        time_stop=end_time,
        types=["message"]
    )
    
    # Process records
    for record in records:
        # Extract user_id from the record
        user_id = record.user_id
        
        if user_id not in user_stats:
            user_stats[user_id] = ChatStatistics(user_id, group_id, days)
        
        user_stats[user_id].add_message(record.time)
    
    return user_stats


async def get_user_active_statistics(user_id: str, group_id: str, days: int) -> ActiveStatistics:
    """Get active time statistics for a user in a specific group over the past N days"""
    
    stats = ActiveStatistics(user_id, group_id, days)
    
    # Calculate time range - use UTC as required by chatrecorder
    end_time = datetime.datetime.now(datetime.timezone.utc)
    start_time = end_time - datetime.timedelta(days=days)
    
    # Get message records using the chatrecorder interface
    records = await get_message_records(
        user_ids=[user_id],
        scene_ids=[group_id],
        time_start=start_time,
        time_stop=end_time,
        types=["message"]
    )
    
    # Process the messages to build statistics
    for record in records:
        stats.add_message(record.time)
    
    return stats


async def get_group_active_statistics(group_id: str, days: int) -> Dict[str, ActiveStatistics]:
    """Get active time statistics for all users in a group over the past N days"""
    
    # Calculate time range - use UTC as required by chatrecorder
    end_time = datetime.datetime.now(datetime.timezone.utc)
    start_time = end_time - datetime.timedelta(days=days)
    
    user_stats: Dict[str, ActiveStatistics] = {}
    
    # Get all message records for the group in the time range
    records = await get_message_records(
        scene_ids=[group_id],
        time_start=start_time,
        time_stop=end_time,
        types=["message"]
    )
    
    # Process records
    for record in records:
        # Extract user_id from the record
        user_id = record.user_id
        
        if user_id not in user_stats:
            user_stats[user_id] = ActiveStatistics(user_id, group_id, days)
        
        user_stats[user_id].add_message(record.time)
    
    return user_stats