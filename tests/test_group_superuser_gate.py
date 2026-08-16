import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import nonebot
from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent
from nonebot.adapters.onebot.v11.event import Sender

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "src" / "plugins"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init()

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
        return await group_superuser_gate._group_has_superuser(bot, FakeEvent())


def private_event():
    return PrivateMessageEvent(
        time=1,
        self_id=100,
        post_type="message",
        sub_type="friend",
        user_id=20,
        message_type="private",
        message_id=300,
        message=Message("test"),
        raw_message="test",
        font=0,
        sender=Sender(nickname="test"),
        to_me=False,
    )


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


@pytest.mark.parametrize("enabled", [True, False])
async def test_private_access_follows_toggle(bot, enabled):
    with patch.object(
        group_superuser_gate,
        "pconfig",
        SimpleNamespace(group_superuser_gate_private_enabled=enabled),
    ):
        assert (
            await group_superuser_gate.event_access_allowed(bot, private_event())
            is enabled
        )

    bot.get_group_member_list.assert_not_awaited()


async def test_non_message_event_is_rejected(bot):
    assert not await group_superuser_gate.event_access_allowed(bot, SimpleNamespace())
