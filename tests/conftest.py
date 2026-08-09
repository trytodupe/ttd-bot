"""
Pytest configuration for NoneBot testing.
"""

import os

import pytest
import nonebot
from nonebug import NONEBOT_INIT_KWARGS

# Set test environment
os.environ["ENVIRONMENT"] = "test"


def pytest_configure(config: pytest.Config):
    """Configure NoneBot initialization for testing."""
    # Use the existing data directory for database access
    config.stash[NONEBOT_INIT_KWARGS] = {
        "superusers": {"12345"},  # Test superuser
    }


@pytest.fixture(scope="session", autouse=True)
async def after_nonebot_init(after_nonebot_init: None):
    """Register the production adapter after NoneBot initialization."""
    from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)
