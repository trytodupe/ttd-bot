import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "src" / "plugins"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

group_superuser_gate = importlib.import_module("group_superuser_gate")


class FakeEvent:
    group_id = 200
    message_id = 300


@pytest.fixture(autouse=True)
def clear_gate_cache():
    group_superuser_gate._event_access_cache.clear()


@pytest.fixture
def bot():
    return SimpleNamespace(
        self_id=100,
        adapter=SimpleNamespace(get_name=lambda: "OneBot V11"),
        get_group_member_list=AsyncMock(),
    )


async def check(bot, superusers):
    with patch.object(
        group_superuser_gate,
        "get_driver",
        return_value=SimpleNamespace(config=SimpleNamespace(superusers=superusers)),
    ):
        return await group_superuser_gate.group_has_superuser(bot, FakeEvent())


@pytest.mark.parametrize("superusers", [{"20"}, {"onebot:20"}])
async def test_allows_bare_and_adapter_scoped_superusers(bot, superusers):
    bot.get_group_member_list.return_value = [{"user_id": 9}, {"user_id": 20}]

    assert await check(bot, superusers)


async def test_rejects_group_without_superuser(bot):
    bot.get_group_member_list.return_value = [{"user_id": 9}]

    assert not await check(bot, {"20"})


async def test_rejects_without_configuration_without_query(bot):
    assert not await check(bot, set())
    bot.get_group_member_list.assert_not_awaited()


async def test_rejects_when_member_query_fails(bot):
    bot.get_group_member_list.side_effect = RuntimeError("unsupported")

    assert not await check(bot, {"20"})


async def test_reuses_result_for_same_event(bot):
    bot.get_group_member_list.return_value = [{"user_id": 20}]

    assert await check(bot, {"20"})
    assert await check(bot, {"20"})
    bot.get_group_member_list.assert_awaited_once_with(group_id=200)
