"""
Tests for the query cache module in nonebot-plugin-learning-chat.

This test file validates:
1. CacheEntry.make_key() generation
2. HotColdCache.get() and put() operations
3. Weak matching logic (_can_use_cache)
4. Time overlap calculation
5. LRU eviction (hot -> cold -> evict)
6. File persistence
"""

import pickle
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest


# Mock ChatMessage for testing
@dataclass
class MockMessage:
    """Mock ChatMessage for testing cache operations."""

    user_id: int
    plain_text: str
    time: int
    group_id: int = 123456


class TestCacheEntry:
    """Test CacheEntry class."""

    def test_make_key_all_fields(self):
        """Test key generation with all fields populated."""
        from nonebot_plugin_learning_chat.cache import CacheEntry

        entry = CacheEntry(
            group_id=123,
            user_id=456,
            content="hello",
            regex=r"test.*",
            time_after=1000,
            time_before=2000,
        )
        key = entry.make_key()
        assert key == "123:456:hello:test.*:1000:2000"

    def test_make_key_none_fields(self):
        """Test key generation with None fields."""
        from nonebot_plugin_learning_chat.cache import CacheEntry

        entry = CacheEntry(
            group_id=123,
            user_id=None,
            content=None,
            regex=None,
            time_after=None,
            time_before=None,
        )
        key = entry.make_key()
        assert key == "123:None:None:None:None:None"

    def test_copy_filter_only(self):
        """Test copying entry without messages."""
        from nonebot_plugin_learning_chat.cache import CacheEntry

        messages = [MockMessage(user_id=1, plain_text="test", time=1000)]
        entry = CacheEntry(
            group_id=123,
            user_id=456,
            content="hello",
            regex=None,
            time_after=1000,
            time_before=2000,
            messages=messages,
            total_count=100,
        )
        copy = entry.copy_filter_only()

        assert copy.group_id == 123
        assert copy.user_id == 456
        assert copy.content == "hello"
        assert copy.messages == []
        assert copy.total_count == 0


class TestQueryFilter:
    """Test QueryFilter class."""

    def test_query_filter_defaults(self):
        """Test QueryFilter default values."""
        from nonebot_plugin_learning_chat.cache import QueryFilter

        qf = QueryFilter()
        assert qf.group_id is None
        assert qf.user_id is None
        assert qf.content is None
        assert qf.regex is None
        assert qf.time_after is None
        assert qf.time_before is None
        assert qf.limit == 20


class TestCacheMatchType:
    """Test CacheMatchType enum."""

    def test_enum_values(self):
        """Test enum values are distinct."""
        from nonebot_plugin_learning_chat.cache import CacheMatchType

        assert CacheMatchType.NO_MATCH.value == 0
        assert CacheMatchType.EXACT_MATCH.value == 1
        assert CacheMatchType.FUZZY_TIME.value == 2
        assert CacheMatchType.PARTIAL_OVERLAP.value == 3


class TestHotColdCacheBasic:
    """Test basic HotColdCache operations."""

    def test_put_and_get_exact_match(self):
        """Test putting and getting cache entry with exact match."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            messages = [MockMessage(user_id=1, plain_text="hello", time=1500)]
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="hello",
                regex=None,
                time_after=1000,
                time_before=2000,
                messages=messages,
                total_count=1,
            )
            cache.put(entry)

            # Query with same parameters
            query = QueryFilter(
                group_id=123,
                content="hello",
                time_after=1000,
                time_before=2000,
                limit=20,
            )
            result = cache.get(query)

            assert result is not None
            assert len(result.messages) == 1
            assert result.is_fuzzy is False
            assert result.needs_incremental is False

    def test_get_no_match(self):
        """Test get returns None when no match."""
        from nonebot_plugin_learning_chat.cache import HotColdCache, QueryFilter

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            query = QueryFilter(group_id=999, content="nonexistent", limit=20)
            result = cache.get(query)

            assert result is None

    def test_clear_cache(self):
        """Test clearing all cache entries."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=None,
                time_before=None,
                messages=[],
                total_count=0,
            )
            cache.put(entry)

            cache.clear()

            query = QueryFilter(group_id=123, content="test", limit=20)
            assert cache.get(query) is None

    def test_stats(self):
        """Test cache statistics."""
        from nonebot_plugin_learning_chat.cache import CacheEntry, HotColdCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=2, cold_size=5, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Add 3 entries (will demote 1 to cold)
            for i in range(3):
                entry = CacheEntry(
                    group_id=i,
                    user_id=None,
                    content=f"test{i}",
                    regex=None,
                    time_after=None,
                    time_before=None,
                    messages=[],
                    total_count=0,
                )
                cache.put(entry)

            stats = cache.stats()
            assert stats["hot_count"] == 2
            assert stats["cold_count"] == 1


class TestWeakMatching:
    """Test weak matching logic."""

    def test_group_id_must_match(self):
        """Test that group_id must be exactly equal."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=None,
                time_before=None,
                messages=[MockMessage(user_id=1, plain_text="test", time=1000)],
                total_count=1,
            )
            cache.put(entry)

            # Different group_id should not match
            query = QueryFilter(group_id=999, content="test", limit=20)
            assert cache.get(query) is None

    def test_regex_must_match(self):
        """Test that regex must be exactly equal."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content=None,
                regex=r"hello.*",
                time_after=None,
                time_before=None,
                messages=[],
                total_count=0,
            )
            cache.put(entry)

            # Different regex should not match
            query = QueryFilter(group_id=123, regex=r"world.*", limit=20)
            assert cache.get(query) is None

            # Same regex should match
            query2 = QueryFilter(group_id=123, regex=r"hello.*", limit=20)
            assert cache.get(query2) is not None

    def test_user_id_weak_match(self):
        """Test that cache with user_id=None matches any user_id query."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Cache entry without user filter
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=None,
                time_before=None,
                messages=[
                    MockMessage(user_id=1, plain_text="test1", time=1000),
                    MockMessage(user_id=2, plain_text="test2", time=1001),
                ],
                total_count=2,
            )
            cache.put(entry)

            # Query with specific user should match and filter
            query = QueryFilter(group_id=123, content="test", user_id=1, limit=20)
            result = cache.get(query)

            assert result is not None
            # Should filter to only user_id=1
            assert len(result.messages) == 1
            assert result.messages[0].user_id == 1

    def test_user_id_strict_match(self):
        """Test that cache with user_id cannot match different user_id query."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Cache entry with specific user
            entry = CacheEntry(
                group_id=123,
                user_id=1,
                content="test",
                regex=None,
                time_after=None,
                time_before=None,
                messages=[MockMessage(user_id=1, plain_text="test", time=1000)],
                total_count=1,
            )
            cache.put(entry)

            # Query with different user should not match
            query = QueryFilter(group_id=123, content="test", user_id=2, limit=20)
            assert cache.get(query) is None

            # Query with same user should match
            query2 = QueryFilter(group_id=123, content="test", user_id=1, limit=20)
            assert cache.get(query2) is not None

    def test_content_substring_match(self):
        """Test that cache content can be substring of query content."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Cache with shorter content
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="hello",
                regex=None,
                time_after=None,
                time_before=None,
                messages=[
                    MockMessage(user_id=1, plain_text="hello world", time=1000),
                    MockMessage(user_id=2, plain_text="hello there", time=1001),
                ],
                total_count=2,
            )
            cache.put(entry)

            # Query with longer content containing cache content
            query = QueryFilter(group_id=123, content="hello world", limit=20)
            result = cache.get(query)

            assert result is not None
            # Should filter to messages containing "hello world"
            assert len(result.messages) == 1
            assert "hello world" in result.messages[0].plain_text

    def test_content_no_match_if_not_substring(self):
        """Test that cache content not in query content returns no match."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="hello",
                regex=None,
                time_after=None,
                time_before=None,
                messages=[],
                total_count=0,
            )
            cache.put(entry)

            # Query with different content
            query = QueryFilter(group_id=123, content="world", limit=20)
            assert cache.get(query) is None


class TestTimeMatching:
    """Test time range matching logic."""

    def test_exact_time_coverage(self):
        """Test cache fully covers query time range."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Cache covers 1000-3000
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=1000,
                time_before=3000,
                messages=[MockMessage(user_id=1, plain_text="test", time=2000)],
                total_count=1,
            )
            cache.put(entry)

            # Query for 1500-2500 (subset of cache range)
            query = QueryFilter(
                group_id=123,
                content="test",
                time_after=1500,
                time_before=2500,
                limit=20,
            )
            result = cache.get(query)

            assert result is not None
            assert result.is_fuzzy is False
            assert result.needs_incremental is False

    def test_fuzzy_time_match(self):
        """Test fuzzy time match when no absolute time and high overlap with small gap."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            now = int(time.time())
            # Cache covers 1 hour, ending 2 minutes ago
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=now - 3600,
                time_before=now - 120,  # 2 minutes ago
                messages=[MockMessage(user_id=1, plain_text="test", time=now - 1800)],
                total_count=1,
            )
            cache.put(entry)

            # Query without time_before (implies "now"), no absolute time
            # Overlap: (now-120) - (now-3600) = 3480 seconds
            # Query duration: now - (now-3600) = 3600 seconds
            # Overlap ratio: 3480/3600 = 96.7% > 90%
            # Uncovered: 120 seconds < 600 seconds
            # => Should trigger FUZZY_TIME
            query = QueryFilter(
                group_id=123,
                content="test",
                time_after=now - 3600,
                limit=20,
                has_absolute_time=False,
            )
            result = cache.get(query)

            assert result is not None
            assert result.is_fuzzy is True
            assert result.needs_incremental is False

    def test_fuzzy_time_no_match_with_absolute_time(self):
        """Test no fuzzy match when absolute time is specified."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            now = int(time.time())
            # Cache covers 1 hour, ending 2 minutes ago
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=now - 3600,
                time_before=now - 120,  # 2 minutes ago
                messages=[MockMessage(user_id=1, plain_text="test", time=now - 1800)],
                total_count=1,
            )
            cache.put(entry)

            # Query with has_absolute_time=True (user specified absolute time)
            # Even though overlap is high, should NOT trigger fuzzy match
            query = QueryFilter(
                group_id=123,
                content="test",
                time_after=now - 3600,
                limit=20,
                has_absolute_time=True,  # User specified absolute time
            )
            result = cache.get(query)

            # Should trigger incremental query instead of fuzzy match
            assert result is not None
            assert result.is_fuzzy is False
            assert result.needs_incremental is True

    def test_fuzzy_time_no_match_outside_window(self):
        """Test no fuzzy match when uncovered duration > 10 minutes."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            now = int(time.time())
            # Cache with time_before = now - 15 minutes
            # Uncovered duration = 15 min = 900 seconds > 600 seconds
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=now - 3600,
                time_before=now - 900,  # 15 minutes ago
                messages=[MockMessage(user_id=1, plain_text="test", time=now - 1800)],
                total_count=1,
            )
            cache.put(entry)

            # Query without time_before, no absolute time
            # Overlap ratio: (3600-900)/3600 = 75% < 90%
            # Uncovered: 900 seconds > 600 seconds
            # => Should NOT trigger FUZZY_TIME
            query = QueryFilter(
                group_id=123,
                content="test",
                time_after=now - 3600,
                limit=20,
                has_absolute_time=False,
            )
            result = cache.get(query)

            # Should trigger incremental query (overlap > 40%)
            if result is not None:
                assert result.is_fuzzy is False
                assert result.needs_incremental is True

    def test_partial_overlap_incremental(self):
        """Test partial overlap triggers incremental query."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Cache covers 1000-2000
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=1000,
                time_before=2000,
                messages=[MockMessage(user_id=1, plain_text="test", time=1500)],
                total_count=1,
            )
            cache.put(entry)

            # Query for 1500-2500 (50% overlap, > 40% threshold)
            query = QueryFilter(
                group_id=123,
                content="test",
                time_after=1500,
                time_before=2500,
                limit=20,
            )
            result = cache.get(query)

            assert result is not None
            assert result.needs_incremental is True
            assert len(result.missing_ranges) > 0
            # Should need to query 2001-2500
            assert (2001, 2500) in result.missing_ranges

    def test_no_overlap_no_match(self):
        """Test no overlap returns no match."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Cache covers 1000-2000
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=1000,
                time_before=2000,
                messages=[],
                total_count=0,
            )
            cache.put(entry)

            # Query for 3000-4000 (no overlap)
            query = QueryFilter(
                group_id=123,
                content="test",
                time_after=3000,
                time_before=4000,
                limit=20,
            )
            result = cache.get(query)

            assert result is None


class TestOverlapCalculation:
    """Test overlap ratio calculation."""

    def test_full_overlap(self):
        """Test 100% overlap."""
        from nonebot_plugin_learning_chat.cache import CacheEntry, HotColdCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Cache covers 1000-3000
            cache_entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=1000,
                time_before=3000,
            )

            from nonebot_plugin_learning_chat.cache import QueryFilter

            # Query 1500-2500 (fully inside cache)
            query = QueryFilter(
                group_id=123, content="test", time_after=1500, time_before=2500
            )

            ratio = cache._calculate_overlap_ratio(cache_entry, query)
            assert ratio == 1.0

    def test_partial_overlap(self):
        """Test partial overlap calculation."""
        from nonebot_plugin_learning_chat.cache import CacheEntry, HotColdCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Cache covers 1000-2000
            cache_entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=1000,
                time_before=2000,
            )

            from nonebot_plugin_learning_chat.cache import QueryFilter

            # Query 1500-2500 (50% overlap: 1500-2000 / 1500-2500)
            query = QueryFilter(
                group_id=123, content="test", time_after=1500, time_before=2500
            )

            ratio = cache._calculate_overlap_ratio(cache_entry, query)
            assert ratio == 0.5

    def test_no_overlap(self):
        """Test no overlap returns 0."""
        from nonebot_plugin_learning_chat.cache import CacheEntry, HotColdCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            cache_entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=1000,
                time_before=2000,
            )

            from nonebot_plugin_learning_chat.cache import QueryFilter

            query = QueryFilter(
                group_id=123, content="test", time_after=3000, time_before=4000
            )

            ratio = cache._calculate_overlap_ratio(cache_entry, query)
            assert ratio == 0.0


class TestLRUEviction:
    """Test LRU eviction behavior."""

    def test_hot_to_cold_demotion(self):
        """Test that hot cache demotes to cold when full."""
        from nonebot_plugin_learning_chat.cache import CacheEntry, HotColdCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=2, cold_size=5, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Add 3 entries
            for i in range(3):
                entry = CacheEntry(
                    group_id=i,
                    user_id=None,
                    content=f"test{i}",
                    regex=None,
                    time_after=None,
                    time_before=None,
                    messages=[],
                    total_count=0,
                )
                cache.put(entry)

            # Hot should have 2, cold should have 1
            assert len(cache.hot_cache) == 2
            assert len(cache.cold_cache) == 1

            # First entry (group_id=0) should be in cold
            cold_keys = list(cache.cold_cache.keys())
            assert "0:None:test0:None:None:None" in cold_keys

    def test_cold_eviction(self):
        """Test that cold cache evicts when full."""
        from nonebot_plugin_learning_chat.cache import CacheEntry, HotColdCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=2, cold_size=3, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Add 6 entries (will fill hot=2, cold=3, evict 1)
            for i in range(6):
                entry = CacheEntry(
                    group_id=i,
                    user_id=None,
                    content=f"test{i}",
                    regex=None,
                    time_after=None,
                    time_before=None,
                    messages=[],
                    total_count=0,
                )
                cache.put(entry)

            # Hot should have 2, cold should have 3
            assert len(cache.hot_cache) == 2
            assert len(cache.cold_cache) == 3

            # Entries 0 should be evicted
            all_keys = list(cache.hot_cache.keys()) + list(cache.cold_cache.keys())
            assert "0:None:test0:None:None:None" not in all_keys

    def test_access_promotes_to_hot(self):
        """Test that accessing cold entry promotes it to hot."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=2, cold_size=5, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Add 3 entries, first will be in cold
            for i in range(3):
                entry = CacheEntry(
                    group_id=i,
                    user_id=None,
                    content="test",
                    regex=None,
                    time_after=None,
                    time_before=None,
                    messages=[MockMessage(user_id=1, plain_text="test", time=1000)],
                    total_count=1,
                )
                cache.put(entry)

            # Entry 0 should be in cold
            assert "0:None:test:None:None:None" in cache.cold_cache

            # Access entry 0
            query = QueryFilter(group_id=0, content="test", limit=20)
            cache.get(query)

            # Entry 0 should now be in hot
            assert "0:None:test:None:None:None" in cache.hot_cache
            assert "0:None:test:None:None:None" not in cache.cold_cache


class TestFilePersistence:
    """Test file persistence of cold cache."""

    def test_save_and_load(self):
        """Test cold cache is saved and loaded correctly."""
        from nonebot_plugin_learning_chat.cache import CacheEntry, HotColdCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.pkl"

            # Create cache and add entries
            cache1 = HotColdCache(hot_size=2, cold_size=5, cache_file=cache_file)
            for i in range(4):
                entry = CacheEntry(
                    group_id=i,
                    user_id=None,
                    content=f"test{i}",
                    regex=None,
                    time_after=None,
                    time_before=None,
                    messages=[],
                    total_count=0,
                )
                cache1.put(entry)

            # Verify file exists
            assert cache_file.exists()

            # Create new cache instance (should load from file)
            cache2 = HotColdCache(hot_size=2, cold_size=5, cache_file=cache_file)

            # All 4 entries should be loaded (hot + cold are both persisted now)
            # They are loaded into cold_cache on startup
            assert len(cache2.cold_cache) == 4

    def test_corrupted_file_handled(self):
        """Test corrupted cache file is handled gracefully."""
        from nonebot_plugin_learning_chat.cache import HotColdCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "cache.pkl"

            # Write corrupted data
            with open(cache_file, "wb") as f:
                f.write(b"corrupted data")

            # Should not raise, cold_cache should be empty
            cache = HotColdCache(hot_size=2, cold_size=5, cache_file=cache_file)
            assert len(cache.cold_cache) == 0


class TestMaxMessagesLimit:
    """Test max messages per entry limit."""

    def test_truncate_on_put(self):
        """Test messages are truncated on put."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            MAX_MESSAGES_PER_ENTRY,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Create entry with more than max messages
            messages = [
                MockMessage(user_id=1, plain_text=f"msg{i}", time=i)
                for i in range(MAX_MESSAGES_PER_ENTRY + 100)
            ]
            entry = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=None,
                time_before=None,
                messages=messages,
                total_count=len(messages),
            )

            cache.put(entry)

            # Get the entry back
            key = entry.make_key()
            stored = cache.hot_cache[key]

            assert len(stored.messages) == MAX_MESSAGES_PER_ENTRY


class TestUpdateEntry:
    """Test update_entry for incremental merge."""

    def test_update_replaces_entry(self):
        """Test update_entry removes old and adds new entry."""
        from nonebot_plugin_learning_chat.cache import (
            CacheEntry,
            HotColdCache,
            QueryFilter,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = HotColdCache(
                hot_size=5, cold_size=10, cache_file=Path(tmpdir) / "cache.pkl"
            )

            # Add original entry
            entry1 = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=1000,
                time_before=2000,
                messages=[MockMessage(user_id=1, plain_text="old", time=1500)],
                total_count=1,
            )
            cache.put(entry1)
            old_key = entry1.make_key()

            # Update with new entry (different time range)
            entry2 = CacheEntry(
                group_id=123,
                user_id=None,
                content="test",
                regex=None,
                time_after=1000,
                time_before=3000,  # Extended time range
                messages=[
                    MockMessage(user_id=1, plain_text="old", time=1500),
                    MockMessage(user_id=1, plain_text="new", time=2500),
                ],
                total_count=2,
            )
            cache.update_entry(old_key, entry2)

            # Old key should not exist
            assert old_key not in cache.hot_cache
            assert old_key not in cache.cold_cache

            # New entry should exist
            new_key = entry2.make_key()
            assert new_key in cache.hot_cache
            assert len(cache.hot_cache[new_key].messages) == 2
