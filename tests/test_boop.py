"""Integration-style tests for the boop plugin.

Tests drive the registered matcher through the nonebug event-handling seam,
mocking only the clock and the OneBot API boundary.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import (
    Adapter,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebug import App


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    *,
    user_id: int = 111,
    group_id: int = 100,
    message: Message,
    to_me: bool = False,
) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1000,
        self_id=999,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message="",
        font=0,
        sender={"user_id": user_id, "nickname": "test"},
        group_id=group_id,
        to_me=to_me,
    )


# ---------------------------------------------------------------------------
# Module fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def boop_module():
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init(superusers={"12345"})

    try:
        nonebot.get_driver().register_adapter(Adapter)
    except ValueError:
        pass

    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    plugin_dir_text = str(plugin_dir)
    if plugin_dir_text not in sys.path:
        sys.path.insert(0, plugin_dir_text)

    module_name = "boop"
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


# ===================================================================
# Rule matching
# ===================================================================

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/boop ", True),
        ("/boop", True),
        ("/booper", False),
        ("/booper @678", False),
        ("some /boop @678", False),
        ("boop @678", False),
        ("boop", False),
        ("hello", False),
        ("", False),
    ],
)
def test_is_boop_command(boop_module, text, expected):
    event = _make_event(message=Message(text))
    assert boop_module._is_boop_command(event) is expected


# ===================================================================
# Valid delivery uses the triggering bot and starts cooldown
# ===================================================================

@pytest.mark.asyncio
async def test_valid_boop_sends_private_and_starts_cooldown(
    boop_module, monkeypatch, app: App
):
    module = boop_module
    module._cooldowns.clear()
    module._sender_locks.clear()
    fake_time = [1000.0]
    monkeypatch.setattr(
        module, "_time", type("_T", (), {"monotonic": staticmethod(lambda: fake_time[0])})()
    )

    event = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(678)]),
    )

    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("send_private_msg", {"user_id": 678, "message": "Boop!"})
        ctx.should_call_send(event, "Boop! 已发送给 678")
        ctx.should_finished()
        await ctx.run()

    assert module._cooldowns[111] == 1000.0


@pytest.mark.asyncio
async def test_concurrent_boops_from_same_sender_are_serialized(
    boop_module, monkeypatch
):
    module = boop_module
    module._cooldowns.clear()
    module._sender_locks.clear()
    monkeypatch.setattr(
        module, "_time", type("_T", (), {"monotonic": staticmethod(lambda: 1000.0)})()
    )

    api_started = asyncio.Event()
    release_api = asyncio.Event()
    api_targets: list[int] = []
    replies: list[str] = []

    class FakeBot:
        async def call_api(self, api: str, **data) -> None:
            assert api == "send_private_msg"
            api_targets.append(data["user_id"])
            api_started.set()
            await release_api.wait()

    class FakeMatcher:
        async def finish(self, message: str) -> None:
            replies.append(message)

    first = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(678)]),
    )
    second = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(888)]),
    )
    bot = FakeBot()
    matcher = FakeMatcher()

    first_task = asyncio.create_task(module._handle_boop(bot, first, matcher))
    await api_started.wait()
    second_task = asyncio.create_task(module._handle_boop(bot, second, matcher))
    await asyncio.sleep(0)

    assert api_targets == [678]

    release_api.set()
    await asyncio.gather(first_task, second_task)

    assert api_targets == [678]
    assert replies == ["Boop! 已发送给 678", module._COOLDOWN_NOTICE]


# ===================================================================
# Different self_id proves event-injected bot is used
# ===================================================================

@pytest.mark.asyncio
async def test_handler_uses_event_bot_for_api_call(boop_module, monkeypatch, app: App):
    module = boop_module
    module._cooldowns.clear()
    monkeypatch.setattr(
        module, "_time", type("_T", (), {"monotonic": staticmethod(lambda: 500.0)})()
    )

    event = _make_event(
        user_id=222,
        group_id=200,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(444)]),
    )

    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="777")
        ctx.receive_event(bot, event)
        ctx.should_call_api("send_private_msg", {"user_id": 444, "message": "Boop!"})
        ctx.should_call_send(event, "Boop! 已发送给 444")
        ctx.should_finished()
        await ctx.run()

    assert module._cooldowns[222] == 500.0


# ===================================================================
# Embedded /boop and prefix /booper must not match
# ===================================================================

@pytest.mark.asyncio
async def test_embedded_boop_does_not_trigger(boop_module, monkeypatch, app: App):
    module = boop_module
    module._cooldowns.clear()

    event = _make_event(
        message=Message([MessageSegment.text("some /boop "), MessageSegment.at(678)]),
    )

    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event)
        ctx.should_not_pass_rule()
        await ctx.run()

    assert module._cooldowns == {}


@pytest.mark.asyncio
async def test_booper_prefix_does_not_trigger(boop_module, monkeypatch, app: App):
    module = boop_module
    module._cooldowns.clear()

    event = _make_event(
        message=Message([MessageSegment.text("/booper "), MessageSegment.at(678)]),
    )

    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event)
        ctx.should_not_pass_rule()
        await ctx.run()

    assert module._cooldowns == {}


# ===================================================================
# Missing target -> usage error
# ===================================================================

@pytest.mark.asyncio
async def test_missing_target_returns_usage_error(boop_module, monkeypatch, app: App):
    module = boop_module
    module._cooldowns.clear()

    event = _make_event(message=Message("/boop"))

    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "用法：/boop @目标（请 @ 一个用户）")
        ctx.should_finished()
        await ctx.run()


# ===================================================================
# Multiple targets -> error
# ===================================================================

@pytest.mark.asyncio
async def test_multiple_targets_returns_error(boop_module, monkeypatch, app: App):
    module = boop_module
    module._cooldowns.clear()

    event = _make_event(
        message=Message([
            MessageSegment.text("/boop "),
            MessageSegment.at(678),
            MessageSegment.at(999),
        ]),
    )

    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "一次只能 Boop 一个用户哦~")
        ctx.should_finished()
        await ctx.run()


# ===================================================================
# Self-target -> error
# ===================================================================

@pytest.mark.asyncio
async def test_self_target_returns_error(boop_module, monkeypatch, app: App):
    module = boop_module
    module._cooldowns.clear()

    event = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(111)]),
    )

    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "你不能 Boop 自己哦~")
        ctx.should_finished()
        await ctx.run()


# ===================================================================
# Cooldown: same sender blocked even with different receiver
# ===================================================================

@pytest.mark.asyncio
async def test_cooldown_blocks_same_sender_different_receiver(
    boop_module, monkeypatch, app: App
):
    module = boop_module
    module._cooldowns.clear()
    fake_time = [1000.0]
    monkeypatch.setattr(
        module, "_time", type("_T", (), {"monotonic": staticmethod(lambda: fake_time[0])})()
    )

    event1 = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(678)]),
    )
    event2 = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(888)]),
    )

    # First boop -- succeeds
    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event1)
        ctx.should_call_api("send_private_msg", {"user_id": 678, "message": "Boop!"})
        ctx.should_call_send(event1, "Boop! 已发送给 678")
        ctx.should_finished()
        await ctx.run()

    assert module._cooldowns[111] == 1000.0

    # Second boop at t+5 with different receiver -- still blocked
    fake_time[0] = 1005.0
    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event2)
        ctx.should_call_send(event2, module._COOLDOWN_NOTICE)
        ctx.should_finished()
        await ctx.run()


# ===================================================================
# Different senders can immediately target the same receiver
# ===================================================================

@pytest.mark.asyncio
async def test_different_senders_same_receiver_no_cooldown(
    boop_module, monkeypatch, app: App
):
    module = boop_module
    module._cooldowns.clear()
    fake_time = [1000.0]
    monkeypatch.setattr(
        module, "_time", type("_T", (), {"monotonic": staticmethod(lambda: fake_time[0])})()
    )

    event_a = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(678)]),
    )
    event_b = _make_event(
        user_id=222,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(678)]),
    )

    # Sender A boops receiver X
    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event_a)
        ctx.should_call_api("send_private_msg", {"user_id": 678, "message": "Boop!"})
        ctx.should_call_send(event_a, "Boop! 已发送给 678")
        ctx.should_finished()
        await ctx.run()

    # Sender B immediately boops same receiver X
    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event_b)
        ctx.should_call_api("send_private_msg", {"user_id": 678, "message": "Boop!"})
        ctx.should_call_send(event_b, "Boop! 已发送给 678")
        ctx.should_finished()
        await ctx.run()

    assert module._cooldowns[111] == 1000.0
    assert module._cooldowns[222] == 1000.0


# ===================================================================
# API failure must not consume the cooldown
# ===================================================================

@pytest.mark.asyncio
async def test_api_failure_does_not_consume_cooldown(
    boop_module, monkeypatch, app: App
):
    module = boop_module
    module._cooldowns.clear()
    monkeypatch.setattr(
        module, "_time", type("_T", (), {"monotonic": staticmethod(lambda: 1000.0)})()
    )

    event = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(678)]),
    )

    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_private_msg",
            {"user_id": 678, "message": "Boop!"},
            exception=RuntimeError("API failure"),
        )
        ctx.should_call_send(event, "私聊发送失败，可能对方未开启私聊权限。")
        ctx.should_finished()
        await ctx.run()

    assert 111 not in module._cooldowns


# ===================================================================
# Exact 10-second cooldown expiry boundary
# ===================================================================

@pytest.mark.asyncio
async def test_cooldown_expires_at_exact_10s(boop_module, monkeypatch, app: App):
    module = boop_module
    module._cooldowns.clear()
    fake_time = [0.0]
    monkeypatch.setattr(
        module, "_time", type("_T", (), {"monotonic": staticmethod(lambda: fake_time[0])})()
    )

    # First boop at t=0
    event1 = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(678)]),
    )
    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event1)
        ctx.should_call_api("send_private_msg", {"user_id": 678, "message": "Boop!"})
        ctx.should_call_send(event1, "Boop! 已发送给 678")
        ctx.should_finished()
        await ctx.run()

    assert module._cooldowns[111] == 0.0

    # At exactly t=10 -- cooldown expired, should succeed
    fake_time[0] = 10.0
    event2 = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(888)]),
    )
    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event2)
        ctx.should_call_api("send_private_msg", {"user_id": 888, "message": "Boop!"})
        ctx.should_call_send(event2, "Boop! 已发送给 888")
        ctx.should_finished()
        await ctx.run()

    assert module._cooldowns[111] == 10.0


# ===================================================================
# API failure then retry succeeds, cooldown only from success
# ===================================================================

@pytest.mark.asyncio
async def test_failed_then_success_sets_cooldown_from_success(
    boop_module, monkeypatch, app: App
):
    module = boop_module
    module._cooldowns.clear()
    fake_time = [1000.0]
    monkeypatch.setattr(
        module, "_time", type("_T", (), {"monotonic": staticmethod(lambda: fake_time[0])})()
    )

    event = _make_event(
        user_id=111,
        message=Message([MessageSegment.text("/boop "), MessageSegment.at(678)]),
    )

    # First attempt: API fails
    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event)
        ctx.should_call_api(
            "send_private_msg",
            {"user_id": 678, "message": "Boop!"},
            exception=RuntimeError("API failure"),
        )
        ctx.should_call_send(event, "私聊发送失败，可能对方未开启私聊权限。")
        ctx.should_finished()
        await ctx.run()

    assert 111 not in module._cooldowns

    # Second attempt: succeeds -- cooldown starts from this point
    async with app.test_matcher(module._matcher) as ctx:
        bot = ctx.create_bot(base=Bot, self_id="999")
        ctx.receive_event(bot, event)
        ctx.should_call_api("send_private_msg", {"user_id": 678, "message": "Boop!"})
        ctx.should_call_send(event, "Boop! 已发送给 678")
        ctx.should_finished()
        await ctx.run()

    assert module._cooldowns[111] == 1000.0


# ===================================================================
# Plugin metadata
# ===================================================================

def test_plugin_has_metadata(boop_module):
    assert hasattr(boop_module, "__plugin_meta__")
    assert boop_module.__plugin_meta__.name == "boop"
    assert boop_module.__plugin_meta__.usage == "/boop @目标"
