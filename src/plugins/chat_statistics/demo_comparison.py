"""
Demo script showing the difference between chat statistics and active statistics

This demonstrates the two types of statistics available in the plugin:
1. Chat Statistics (ttd chat) - counts actual message numbers
2. Active Statistics (ttd active) - counts active days per hour
"""

from collections import defaultdict


def demo_comparison():
    print("=== Chat Statistics vs Active Statistics Demo ===\n")
    
    # Simulate a realistic scenario
    print("Scenario: User's activity over 10 days")
    print("-" * 40)
    
    # Mock data: messages sent at different times
    messages = [
        # Day 1: Heavy chatting at 20h (12 messages)
        *[(1, 20) for _ in range(12)],
        # Day 2: Light activity at 20h (2 messages)  
        *[(2, 20) for _ in range(2)],
        # Day 3: No activity
        # Day 4: Active at multiple hours
        *[(4, 9) for _ in range(3)],
        *[(4, 14) for _ in range(5)],
        *[(4, 20) for _ in range(8)],
        # Day 5: Single message at 20h
        (5, 20),
        # Day 6-10: No activity
    ]
    
    print("Message distribution by day and hour:")
    day_hour_counts = defaultdict(lambda: defaultdict(int))
    for day, hour in messages:
        day_hour_counts[day][hour] += 1
    
    for day in range(1, 11):
        if day in day_hour_counts:
            hours_str = ", ".join([f"{h}h({count}条)" for h, count in day_hour_counts[day].items()])
            print(f"Day {day:2d}: {hours_str}")
        else:
            print(f"Day {day:2d}: 无活动")
    
    # Calculate chat statistics (message counts)
    chat_hourly = defaultdict(int)
    for day, hour in messages:
        chat_hourly[hour] += 1
    
    # Calculate active statistics (active days)
    active_hourly = defaultdict(set)
    for day, hour in messages:
        active_hourly[hour].add(day)
    
    active_counts = {hour: len(days) for hour, days in active_hourly.items()}
    
    print(f"\n{'='*60}")
    print("CHAT STATISTICS (ttd chat 10) - 消息数量统计:")
    print(f"{'='*60}")
    total_messages = sum(chat_hourly.values())
    print(f"过去10天聊天分布 (共{total_messages}条消息)")
    
    for hour in range(24):
        count = chat_hourly.get(hour, 0)
        if count > 0:
            percentage = (count / total_messages * 100) if total_messages > 0 else 0
            print(f"{hour:2d}h: {count:3d} ({percentage:4.1f}%)")
    
    print(f"\n{'='*60}")
    print("ACTIVE STATISTICS (ttd active 10) - 活跃天数统计:")
    print(f"{'='*60}")
    total_active_hours = len([h for h, count in active_counts.items() if count > 0])
    print(f"过去10天活跃时间分布 (共{total_active_hours}个活跃小时)")
    
    for hour in range(24):
        count = active_counts.get(hour, 0)
        percentage = (count / 10 * 100) if count > 0 else 0
        if count > 0:
            print(f"{hour:2d}h: {count:3d} ({percentage:5.1f}%)")
    
    print(f"\n{'='*60}")
    print("KEY DIFFERENCES:")
    print(f"{'='*60}")
    print("Chat Statistics:")
    print("- 20h: 23条消息 (76.7%) - 显示消息数量多")
    print("- 重点关注 '说了多少话'")
    print("")
    print("Active Statistics:")  
    print("- 20h: 4天 (40.0%) - 显示有4天在这个时间活跃")
    print("- 9h和14h: 1天 (10.0%) - 只在1天活跃")
    print("- 重点关注 '哪些时间段经常出现'")
    print("")
    print("Use Cases:")
    print("- ttd chat: 找出用户最爱聊天的时间段")
    print("- ttd active: 找出用户最常在线的时间段")
    
    return True


if __name__ == "__main__":
    success = demo_comparison()
    if success:
        print(f"\n{'✓ Demo completed successfully!'}")
        print("Both statistics types are now available in the plugin!")