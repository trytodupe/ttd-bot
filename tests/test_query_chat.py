"""
Tests for the query_chat command in nonebot-plugin-learning-chat.

This test file validates:
1. Argument parsing (key=value format)
2. Time parsing (relative and absolute)
3. Query filter functionality
4. Output formatting
"""

import pytest
from datetime import datetime, timedelta, timezone


# UTC+8 timezone for testing
TZ_UTC8 = timezone(timedelta(hours=8))


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

        ts, is_abs = parse_time("7d")
        assert ts is not None
        assert is_abs is False
        # Should be approximately 7 days ago
        expected = datetime.now(TZ_UTC8) - timedelta(days=7)
        assert abs(ts - int(expected.timestamp())) < 60  # Within 1 minute

    def test_parse_time_relative_hours(self):
        """Test parsing relative time in hours."""
        from nonebot_plugin_learning_chat.query import parse_time

        ts, is_abs = parse_time("24h")
        assert ts is not None
        assert is_abs is False
        expected = datetime.now(TZ_UTC8) - timedelta(hours=24)
        assert abs(ts - int(expected.timestamp())) < 60

    def test_parse_time_absolute_date(self):
        """Test parsing absolute date."""
        from nonebot_plugin_learning_chat.query import parse_time

        ts, is_abs = parse_time("2025-01-01")
        assert ts is not None
        assert is_abs is True
        # Should be 2025-01-01 00:00:00 UTC+8
        expected = datetime(2025, 1, 1, tzinfo=TZ_UTC8)
        assert ts == int(expected.timestamp())

    def test_parse_time_absolute_datetime(self):
        """Test parsing absolute datetime."""
        from nonebot_plugin_learning_chat.query import parse_time

        ts, is_abs = parse_time("2025-01-01 12:30")
        assert ts is not None
        assert is_abs is True
        expected = datetime(2025, 1, 1, 12, 30, tzinfo=TZ_UTC8)
        assert ts == int(expected.timestamp())

    def test_parse_time_invalid(self):
        """Test parsing invalid time returns None."""
        from nonebot_plugin_learning_chat.query import parse_time

        ts, _ = parse_time("invalid")
        assert ts is None
        ts, _ = parse_time("")
        assert ts is None


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

    def test_query_filter_default_after_days(self):
        """Test that after defaults to 30 days ago."""
        import time

        import nonebot_plugin_learning_chat.query as query_module
        from nonebot_plugin_learning_chat.query import QueryFilter

        qf = QueryFilter.from_args({"content": "test"})
        expected_after = int(time.time()) - query_module.DEFAULT_AFTER_DAYS * 24 * 3600

        # Should be within 60 seconds of expected
        assert qf.time_after is not None
        assert abs(qf.time_after - expected_after) < 60

    def test_query_filter_to_cache_filter(self):
        """Test conversion to cache QueryFilter."""
        from nonebot_plugin_learning_chat.query import QueryFilter

        qf = QueryFilter.from_args({"content": "test", "user": "123", "limit": "50"})
        qf.group_id = 456

        cache_filter = qf.to_cache_filter()

        assert cache_filter.group_id == 456
        assert cache_filter.user_id == 123
        assert cache_filter.content == "test"
        assert cache_filter.limit == 50


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


# Note: Database query tests require a running Tortoise ORM connection
# and are skipped by default. They can be run manually for integration testing.
#
# class TestDatabaseQuery:
#     """Test actual database query (requires initialized Tortoise ORM)."""
#
#     @pytest.mark.asyncio
#     async def test_execute_query_with_real_database(self):
#         """Test executing query against the real database."""
#         from tortoise import Tortoise
#         from nonebot_plugin_learning_chat.query import QueryFilter, execute_query_raw
#
#         await Tortoise.init(
#             db_url="sqlite://./data/learning_chat/learning_chat.db",
#             modules={"learning_chat": ["nonebot_plugin_learning_chat.models"]},
#         )
#
#         try:
#             qf = QueryFilter()
#             qf.content = "test"
#             qf.group_id = 1076794521
#             qf.limit = 10
#
#             messages = await execute_query_raw(qf)
#             print(f"Found {len(messages)} messages")
#
#         finally:
#             await Tortoise.close_connections()
