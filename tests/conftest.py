"""
Pytest configuration for NoneBot testing.
"""

import os
import pytest
import nonebot
from pathlib import Path
from pytest_asyncio import is_async_test
from nonebug import NONEBOT_INIT_KWARGS

# Set test environment
os.environ["ENVIRONMENT"] = "test"


def pytest_configure(config: pytest.Config):
    """Configure NoneBot initialization for testing."""
    # Use the existing data directory for database access
    config.stash[NONEBOT_INIT_KWARGS] = {
        "superusers": {"12345"},  # Test superuser
    }


def pytest_collection_modifyitems(items: list[pytest.Item]):
    """Mark all async tests to use session scope."""
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)


@pytest.fixture(scope="session", autouse=True)
async def after_nonebot_init(after_nonebot_init: None):
    """Load adapters and plugins after NoneBot initialization."""
    from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

    # Register adapter
    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)

    # Load only the learning_chat plugin for testing
    nonebot.load_plugin("nonebot_plugin_learning_chat")
