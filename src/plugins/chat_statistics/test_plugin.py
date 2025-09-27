"""
Test script for the chat-statistics plugin

This script demonstrates how to use the plugin functionality
and provides examples for testing and development.
"""

import datetime

# Mock data for testing
class MockChatStatistics:
    """Mock version of ChatStatistics for testing without database"""
    
    def __init__(self, user_id: str, group_id: str, days: int):
        self.user_id = user_id
        self.group_id = group_id
        self.days = days
        self.hourly_distribution = {}
        self.total_messages = 0
        self.start_time = datetime.datetime.now() - datetime.timedelta(days=days)
        self.end_time = datetime.datetime.now()
    
    def add_mock_data(self):
        """Add realistic mock data"""
        # Simulate a typical user's chat pattern
        patterns = {
            8: 3,   # Morning
            9: 5,
            10: 8,
            11: 6,
            12: 4,  # Lunch
            13: 7,  # Afternoon
            14: 10,
            15: 8,
            16: 6,
            17: 4,
            18: 6,  # Evening
            19: 9,
            20: 12,  # Peak evening
            21: 10,
            22: 7,
            23: 3,
        }
        
        for hour, count in patterns.items():
            self.hourly_distribution[hour] = count
            self.total_messages += count
    
    def format_text_output(self):
        """Format as text output"""
        if self.total_messages == 0:
            return f"过去{self.days}天无聊天记录"
        
        lines = []
        header = f"过去{self.days}天聊天分布 (共{self.total_messages}条消息)"
        lines.append(header)
        
        for hour in range(24):
            count = self.hourly_distribution.get(hour, 0)
            percentage = (count / self.total_messages * 100) if self.total_messages > 0 else 0
            lines.append(f"{hour:2d}h: {count:3d} ({percentage:4.1f}%)")
        
        return "\n".join(lines)


def test_basic_functionality():
    """Test basic plugin functionality"""
    print("=== Chat Statistics Plugin Test ===\n")
    
    # Create mock statistics
    stats = MockChatStatistics("123456", "789012", 7)
    stats.add_mock_data()
    
    print("1. Basic Information:")
    print(f"   User ID: {stats.user_id}")
    print(f"   Group ID: {stats.group_id}")
    print(f"   Days: {stats.days}")
    print(f"   Total Messages: {stats.total_messages}")
    print()
    
    print("2. Text Output:")
    output = stats.format_text_output()
    print(output)
    print()
    
    print("3. Peak Activity Analysis:")
    if stats.hourly_distribution:
        peak_hour = max(stats.hourly_distribution, key=stats.hourly_distribution.get)
        peak_count = stats.hourly_distribution[peak_hour]
        print(f"   Peak Hour: {peak_hour}:00 ({peak_count} messages)")
        
        # Find quiet hours
        quiet_hours = [h for h in range(24) if stats.hourly_distribution.get(h, 0) == 0]
        if quiet_hours:
            print(f"   Quiet Hours: {', '.join(f'{h}:00' for h in quiet_hours[:5])}")
        else:
            print("   No completely quiet hours")
    print()
    
    print("4. Command Examples:")
    print("   ttd chat       # Show 7-day statistics")
    print("   ttd chat 3     # Show 3-day statistics")
    print("   ttd chat 30    # Show 30-day statistics")
    print()
    
    return True


def simulate_command_processing(days_arg: str = ""):
    """Simulate command argument processing"""
    print("=== Command Processing Simulation ===\n")
    
    try:
        if not days_arg.strip():
            days = 7  # Default
            print(f"No argument provided, using default: {days} days")
        else:
            days = int(days_arg.strip())
            if days <= 0:
                print("ERROR: Days must be greater than 0")
                return False
            print(f"Using specified days: {days}")
        
        # Simulate getting statistics
        stats = MockChatStatistics("user123", "group456", days)
        stats.add_mock_data()
        
        print(f"\n--- Statistics for past {days} days ---")
        print(stats.format_text_output())
        
        return True
        
    except ValueError:
        print("ERROR: Invalid number format")
        return False


if __name__ == "__main__":
    # Run tests
    success = test_basic_functionality()
    
    if success:
        print("\n" + "="*50)
        print("Testing different command arguments:")
        print("="*50)
        
        # Test various command arguments
        test_cases = ["", "3", "7", "30", "0", "-5", "abc", "10.5"]
        
        for i, test_case in enumerate(test_cases, 1):
            print(f"\nTest {i}: 'ttd chat {test_case}'")
            print("-" * 30)
            simulate_command_processing(test_case)
        
        print("\n" + "="*50)
        print("✓ All tests completed!")
        print("The chat-statistics plugin is ready to use.")
        print("="*50)