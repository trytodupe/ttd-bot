"""Integration tests for the counter_game plugin.

All tests drive the real registered NoneBot matcher via nonebug
``App.test_matcher`` with proper OneBot V11 event objects.

Verified scenarios:
  A. A:1 → silent, B:2 → silent, A:4 → failure/reset, B:1 → silent.
  B. Private digit messages never pass the matcher rule.
  C. Non-numeric text, whitespace-padded numbers, sign-prefixed, decimals
     never pass the matcher rule.
  D. Messages containing at, image, reply, or other non-text segments
     never pass the matcher rule.
  E. Different groups are independent.
"""

import sys
import time as _time

import pytest
from nonebot.adapters.onebot.v11 import (
    Adapter as OneBotV11Adapter,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebug import App


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVENT_SEQ = 0


def _next_seq() -> int:
    global _EVENT_SEQ
    _EVENT_SEQ += 1
    return _EVENT_SEQ


def _make_group_event(
    group_id: int,
    text: str,
    *,
    user_id: int = 100,
    message_id: int | None = None,
) -> GroupMessageEvent:
    msg = Message(text)
    return GroupMessageEvent(
        time=int(_time.time()),
        self_id=999,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=message_id if message_id is not None else _next_seq(),
        message=msg,
        original_message=msg,
        raw_message=text,
        font=0,
        sender={"user_id": user_id, "nickname": f"user_{user_id}"},
        to_me=False,
        group_id=group_id,
    )


def _make_private_event(
    text: str,
    *,
    user_id: int = 100,
    message_id: int | None = None,
) -> PrivateMessageEvent:
    msg = Message(text)
    return PrivateMessageEvent(
        time=int(_time.time()),
        self_id=999,
        post_type="message",
        sub_type="friend",
        user_id=user_id,
        message_type="private",
        message_id=message_id if message_id is not None else _next_seq(),
        message=msg,
        original_message=msg,
        raw_message=text,
        font=0,
        sender={"user_id": user_id, "nickname": f"user_{user_id}"},
    )


def _make_rich_group_event(
    group_id: int,
    segments: list[MessageSegment],
    *,
    user_id: int = 100,
) -> GroupMessageEvent:
    msg = Message(segments)
    return GroupMessageEvent(
        time=int(_time.time()),
        self_id=999,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=_next_seq(),
        message=msg,
        original_message=msg,
        raw_message=str(msg),
        font=0,
        sender={"user_id": user_id, "nickname": f"user_{user_id}"},
        to_me=False,
        group_id=group_id,
    )


@pytest.fixture()
def counter_game_module():
    """Import (or reload) the counter_game plugin and reset its mutable state."""
    import importlib
    import sys
    from pathlib import Path

    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))

    module_name = "counter_game"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)

    # Clear shared state between tests.
    module._group_counters.clear()
    module._group_locks.clear()
    return module


# ---------------------------------------------------------------------------
# A. Integrated counting sequence: 1 → 2 → 4(fail) → 1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_counting_sequence_silent_accept_then_fail_then_recover(
    app: App, counter_game_module
):
    mod = counter_game_module
    matcher = mod.matcher

    async with app.test_matcher(matcher) as ctx:
        adapter = ctx.create_adapter(base=OneBotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter, self_id="999")

        # --- sender A sends "1" (previous=0, expected=1) → silent accept ---
        event_1 = _make_group_event(group_id=1000, text="1", user_id=10)
        ctx.receive_event(bot, event_1)
        ctx.should_pass_rule()
        ctx.should_finished()

        # --- sender B sends "2" (previous=1, expected=2) → silent accept ---
        event_2 = _make_group_event(group_id=1000, text="2", user_id=20)
        ctx.receive_event(bot, event_2)
        ctx.should_pass_rule()
        ctx.should_finished()

        # --- sender A sends "4" (previous=2, expected=3) → failure + reset ---
        event_4 = _make_group_event(group_id=1000, text="4", user_id=10)
        failure_text = "接龙失败！正确数字应为 3，已重置为 0，下一位请发 1。"
        ctx.receive_event(bot, event_4)
        ctx.should_pass_rule()
        ctx.should_call_send(event_4, failure_text)
        ctx.should_finished()

        # --- sender B sends "1" (previous=0 after reset, expected=1) → silent accept ---
        event_recover = _make_group_event(group_id=1000, text="1", user_id=20)
        ctx.receive_event(bot, event_recover)
        ctx.should_pass_rule()
        ctx.should_finished()

    assert mod._group_counters[1000] == 1


# ---------------------------------------------------------------------------
# B. Private digit messages must not pass the rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_private_digit_message_does_not_pass_rule(
    app: App, counter_game_module
):
    mod = counter_game_module
    matcher = mod.matcher

    async with app.test_matcher(matcher) as ctx:
        adapter = ctx.create_adapter(base=OneBotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter, self_id="999")

        event = _make_private_event("1")
        ctx.receive_event(bot, event)
        ctx.should_not_pass_rule()


# ---------------------------------------------------------------------------
# C. Non-numeric text never passes the rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "12abc",
        "+1",
        "-1",
        "1.0",
        "3.14",
        " 1",
        "1 ",
        " 1 ",
        "\t1",
    ],
)
async def test_nonnumeric_messages_do_not_pass_rule(
    app: App, counter_game_module, text: str
):
    mod = counter_game_module
    matcher = mod.matcher

    async with app.test_matcher(matcher) as ctx:
        adapter = ctx.create_adapter(base=OneBotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter, self_id="999")

        event = _make_group_event(group_id=5000, text=text)
        ctx.receive_event(bot, event)
        ctx.should_not_pass_rule()


@pytest.mark.asyncio
async def test_oversized_number_does_not_pass_rule(app: App, counter_game_module):
    mod = counter_game_module
    matcher = mod.matcher
    max_digits = sys.get_int_max_str_digits()
    if max_digits == 0:
        pytest.skip("Python integer string conversion limit is disabled")

    async with app.test_matcher(matcher) as ctx:
        adapter = ctx.create_adapter(base=OneBotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter, self_id="999")

        event = _make_group_event(group_id=5000, text="1" * (max_digits + 1))
        ctx.receive_event(bot, event)
        ctx.should_not_pass_rule()


# ---------------------------------------------------------------------------
# D. Rich-segment group messages must not pass the rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "segments",
    [
        # at segment
        [MessageSegment.at(12345), MessageSegment.text("1")],
        # pure image
        [MessageSegment.image("https://example.com/img.jpg")],
        # text + image
        [
            MessageSegment.text("1"),
            MessageSegment.image("https://example.com/img.jpg"),
        ],
        # reply segment
        [MessageSegment.reply(42)],
        # text + reply
        [MessageSegment.reply(42), MessageSegment.text("1")],
        # only at
        [MessageSegment.at(99999)],
    ],
    ids=[
        "at_and_text",
        "image_only",
        "text_and_image",
        "reply_only",
        "reply_and_text",
        "at_only",
    ],
)
async def test_rich_segment_messages_do_not_pass_rule(
    app: App, counter_game_module, segments: list[MessageSegment]
):
    mod = counter_game_module
    matcher = mod.matcher

    async with app.test_matcher(matcher) as ctx:
        adapter = ctx.create_adapter(base=OneBotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter, self_id="999")

        event = _make_rich_group_event(group_id=6000, segments=segments)
        ctx.receive_event(bot, event)
        ctx.should_not_pass_rule()


# ---------------------------------------------------------------------------
# E. Different groups are independent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_different_groups_are_independent(app: App, counter_game_module):
    mod = counter_game_module
    matcher = mod.matcher

    async with app.test_matcher(matcher) as ctx:
        adapter = ctx.create_adapter(base=OneBotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter, self_id="999")

        # Group A: accept "1"
        ctx.receive_event(
            bot, _make_group_event(group_id=100, text="1", user_id=10)
        )
        ctx.should_pass_rule()
        ctx.should_finished()

        # Group B: accept "1" (independent — starts from 0)
        ctx.receive_event(
            bot, _make_group_event(group_id=200, text="1", user_id=20)
        )
        ctx.should_pass_rule()
        ctx.should_finished()

        # Group A: accept "2"
        ctx.receive_event(
            bot, _make_group_event(group_id=100, text="2", user_id=10)
        )
        ctx.should_pass_rule()
        ctx.should_finished()

        # Group A: "99" fails, resets only group A
        event_fail = _make_group_event(group_id=100, text="99", user_id=10)
        ctx.receive_event(bot, event_fail)
        ctx.should_pass_rule()
        ctx.should_call_send(
            event_fail,
            "接龙失败！正确数字应为 3，已重置为 0，下一位请发 1。",
        )
        ctx.should_finished()

        # Group B: accept "2" (unaffected by group A's reset)
        ctx.receive_event(
            bot, _make_group_event(group_id=200, text="2", user_id=20)
        )
        ctx.should_pass_rule()
        ctx.should_finished()

    assert mod._group_counters[100] == 0
    assert mod._group_counters[200] == 2
