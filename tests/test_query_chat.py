"""
Tests for the query_chat command in nonebot-plugin-learning-chat.

This test file validates:
1. Argument parsing (key=value format)
2. Time parsing (relative and absolute)
3. Permission checks (group vs private, superuser)
4. Query execution and result formatting
"""

import pytest
from datetime import datetime, timedelta, timezone
from nonebug import App
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    GroupMessageEvent,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Sender


# UTC+8 timezone for testing
TZ_UTC8 = timezone(timedelta(hours=8))


def make_group_event(
    message: str,
    user_id: int = 123456,
    group_id: int = 1076794521,
    self_id: int = 999999,
) -> GroupMessageEvent:
    """Create a mock group message event."""
    return GroupMessageEvent(
        time=int(datetime.now().timestamp()),
        self_id=self_id,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1,
        message=Message(message),
        raw_message=message,
        font=0,
        sender=Sender(user_id=user_id, nickname="TestUser"),
        group_id=group_id,
    )


def make_private_event(
    message: str,
    user_id: int = 12345,  # Superuser by default
    self_id: int = 999999,
) -> PrivateMessageEvent:
    """Create a mock private message event."""
    return PrivateMessageEvent(
        time=int(datetime.now().timestamp()),
        self_id=self_id,
        post_type="message",
        sub_type="friend",
        user_id=user_id,
        message_type="private",
        message_id=1,
        message=Message(message),
        raw_message=message,
        font=0,
        sender=Sender(user_id=user_id, nickname="TestUser"),
    )


class TestArgumentParsing:
    """Test the argument parsing functions."""

    def test_parse_query_args_simple(self):
        """Test parsing simple key=value pairs."""
        from nonebot_plugin_learning_chat.query import parse_query_args

        result = parse_query_args('content="hello" user=123')
        assert result["content"] == "hello"
        assert result["user"] == "123"

    def test_parse_query_args_with_spaces(self):
        """Test parsing values with spaces (quoted)."""
        from nonebot_plugin_learning_chat.query import parse_query_args

        result = parse_query_args('content="hello world" limit=50')
        assert result["content"] == "hello world"
        assert result["limit"] == "50"

    def test_parse_query_args_unquoted(self):
        """Test parsing unquoted values."""
        from nonebot_plugin_learning_chat.query import parse_query_args

        result = parse_query_args("content=hello user=456 limit=100")
        assert result["content"] == "hello"
        assert result["user"] == "456"
        assert result["limit"] == "100"

    def test_parse_query_args_single_quotes(self):
        """Test parsing single-quoted values."""
        from nonebot_plugin_learning_chat.query import parse_query_args

        result = parse_query_args("content='test message' regex='.*'")
        assert result["content"] == "test message"
        assert result["regex"] == ".*"


class TestTimeParsing:
    """Test the time parsing functions."""

    def test_parse_time_relative_days(self):
        """Test parsing relative time in days."""
        from nonebot_plugin_learning_chat.query import parse_time

        result = parse_time("7d")
        assert result is not None
        # Should be approximately 7 days ago
        expected = datetime.now(TZ_UTC8) - timedelta(days=7)
        assert abs(result - int(expected.timestamp())) < 60  # Within 1 minute

    def test_parse_time_relative_hours(self):
        """Test parsing relative time in hours."""
        from nonebot_plugin_learning_chat.query import parse_time

        result = parse_time("24h")
        assert result is not None
        expected = datetime.now(TZ_UTC8) - timedelta(hours=24)
        assert abs(result - int(expected.timestamp())) < 60

    def test_parse_time_absolute_date(self):
        """Test parsing absolute date."""
        from nonebot_plugin_learning_chat.query import parse_time

        result = parse_time("2025-01-01")
        assert result is not None
        # Should be 2025-01-01 00:00:00 UTC+8
        expected = datetime(2025, 1, 1, tzinfo=TZ_UTC8)
        assert result == int(expected.timestamp())

    def test_parse_time_absolute_datetime(self):
        """Test parsing absolute datetime."""
        from nonebot_plugin_learning_chat.query import parse_time

        result = parse_time("2025-01-01 12:30")
        assert result is not None
        expected = datetime(2025, 1, 1, 12, 30, tzinfo=TZ_UTC8)
        assert result == int(expected.timestamp())

    def test_parse_time_invalid(self):
        """Test parsing invalid time returns None."""
        from nonebot_plugin_learning_chat.query import parse_time

        assert parse_time("invalid") is None
        assert parse_time("") is None


class TestQueryFilter:
    """Test the QueryFilter class."""

    def test_query_filter_from_args(self):
        """Test creating QueryFilter from arguments."""
        from nonebot_plugin_learning_chat.query import QueryFilter

        args = {
            "content": "test",
            "user": "12345",
            "limit": "100",
        }
        qf = QueryFilter.from_args(args)
        assert qf.content == "test"
        assert qf.user_id == 12345
        assert qf.limit == 100

    def test_query_filter_has_message_filter(self):
        """Test has_message_filter method."""
        from nonebot_plugin_learning_chat.query import QueryFilter

        # With content
        qf1 = QueryFilter.from_args({"content": "test"})
        assert qf1.has_message_filter() is True

        # With regex
        qf2 = QueryFilter.from_args({"regex": ".*"})
        assert qf2.has_message_filter() is True

        # Without either
        qf3 = QueryFilter.from_args({"user": "123"})
        assert qf3.has_message_filter() is False

    def test_query_filter_invalid_regex(self):
        """Test that invalid regex raises ValueError."""
        from nonebot_plugin_learning_chat.query import QueryFilter

        with pytest.raises(ValueError, match="Invalid regex"):
            QueryFilter.from_args({"regex": "[invalid"})

    def test_query_filter_max_limit(self):
        """Test that limit is capped at MAX_LIMIT."""
        from nonebot_plugin_learning_chat.query import QueryFilter, MAX_LIMIT

        qf = QueryFilter.from_args({"content": "test", "limit": "9999"})
        assert qf.limit == MAX_LIMIT


class TestQueryCommand:
    """Test the query_chat command handler."""

    @pytest.mark.asyncio
    async def test_group_query_missing_filter(self, app: App):
        """Test that query without content/regex returns error."""
        from nonebot_plugin_learning_chat.query import query_chat

        async with app.test_matcher(query_chat) as ctx:
            bot = ctx.create_bot()
            event = make_group_event("/query_chat user=123")
            ctx.receive_event(bot, event)
            ctx.should_call_send(
                event,
                "Error: Must specify at least 'content' or 'regex' parameter.\n"
                'Usage: /query_chat content="keyword" [user=123] [after=7d] [limit=50]\n'
                '       /query_chat regex="pattern.*" [user=123]',
                result=None,
            )
            ctx.should_finished(query_chat)

    @pytest.mark.asyncio
    async def test_private_non_superuser_rejected(self, app: App):
        """Test that non-superuser in private chat is rejected."""
        from nonebot_plugin_learning_chat.query import query_chat

        async with app.test_matcher(query_chat) as ctx:
            bot = ctx.create_bot()
            # User 999 is not a superuser (12345 is)
            event = make_private_event('/query_chat content="test"', user_id=999)
            ctx.receive_event(bot, event)
            ctx.should_call_send(
                event,
                "This command is only available to superusers in private chat.",
                result=None,
            )
            ctx.should_finished(query_chat)

    @pytest.mark.asyncio
    async def test_private_superuser_missing_group(self, app: App):
        """Test that superuser in private chat must specify group."""
        from nonebot_plugin_learning_chat.query import query_chat

        async with app.test_matcher(query_chat) as ctx:
            bot = ctx.create_bot()
            # User 12345 is a superuser
            event = make_private_event('/query_chat content="test"', user_id=12345)
            ctx.receive_event(bot, event)
            ctx.should_call_send(
                event,
                "Error: Must specify 'group' parameter in private chat.\n"
                'Usage: /query_chat content="keyword" group=123456789',
                result=None,
            )
            ctx.should_finished(query_chat)

    @pytest.mark.asyncio
    async def test_group_query_uses_current_group(self, app: App):
        """Test that group query automatically uses current group_id."""
        from nonebot_plugin_learning_chat.query import query_chat

        async with app.test_matcher(query_chat) as ctx:
            bot = ctx.create_bot()
            # Use a group that exists in the database
            event = make_group_event(
                '/query_chat content="xyznonexistent" limit=5',
                group_id=1076794521,
            )
            ctx.receive_event(bot, event)
            # Should respond with "No messages found" since the content won't exist
            ctx.should_call_send(
                event,
                'Query: content="xyznonexistent" | group=1076794521 | limit=5\n\n'
                "No messages found.",
                result=None,
            )
            ctx.should_finished(query_chat)


class TestFormatting:
    """Test output formatting functions."""

    def test_truncate_message(self):
        """Test message truncation."""
        from nonebot_plugin_learning_chat.query import truncate_message

        short = "Hello"
        assert truncate_message(short) == "Hello"

        long = "A" * 150
        result = truncate_message(long, max_len=100)
        assert len(result) == 100
        assert result.endswith("...")

    def test_format_timestamp(self):
        """Test timestamp formatting."""
        from nonebot_plugin_learning_chat.query import format_timestamp

        # Test a known timestamp
        ts = 1704067200  # 2024-01-01 00:00:00 UTC
        result = format_timestamp(ts)
        # In UTC+8, this should be 2024-01-01 08:00:00
        assert "2024-01-01" in result

    def test_format_conditions(self):
        """Test QueryFilter.format_conditions."""
        from nonebot_plugin_learning_chat.query import QueryFilter

        qf = QueryFilter()
        qf.content = "test"
        qf.user_id = 123
        qf.limit = 50

        result = qf.format_conditions()
        assert 'content="test"' in result
        assert "user=123" in result
        assert "limit=50" in result


class TestDatabaseQuery:
    """Test actual database query (requires initialized Tortoise ORM)."""

    @pytest.mark.asyncio
    async def test_execute_query_with_real_database(self, app: App):
        """
        Test executing query against the real database.
        This test uses the actual learning_chat.db database.
        """
        from tortoise import Tortoise

        from nonebot_plugin_learning_chat.query import QueryFilter, execute_query

        # Initialize Tortoise ORM to use the real database
        await Tortoise.init(
            db_url="sqlite://./data/learning_chat/learning_chat.db",
            modules={"learning_chat": ["nonebot_plugin_learning_chat.models"]},
        )

        try:
            # Query for messages containing common words
            qf = QueryFilter()
            qf.content = "吃"  # Common Chinese character in messages
            qf.group_id = 1076794521
            qf.limit = 10

            messages = await execute_query(qf)

            # Log results for inspection
            print(f"\n=== Query Results for content='吃' ===")
            print(f"Found {len(messages)} messages")
            for msg in messages[:5]:
                print(f"  [{msg.time}] {msg.user_id}: {msg.plain_text[:50]}...")

            # Test with regex
            qf2 = QueryFilter()
            qf2.regex = r"吃.*饭"  # Match "吃...饭" pattern
            qf2.group_id = 1076794521
            qf2.limit = 10

            messages2 = await execute_query(qf2)
            print(f"\n=== Query Results for regex='吃.*饭' ===")
            print(f"Found {len(messages2)} messages")
            for msg in messages2[:5]:
                print(f"  [{msg.time}] {msg.user_id}: {msg.plain_text[:50]}...")

        finally:
            await Tortoise.close_connections()
