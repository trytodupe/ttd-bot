import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent


def _protocol():
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init(superusers={"12345"})
    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    return importlib.import_module("dev_agent_gateway.protocol")


def _private(message: Message, message_id: int = 1) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        time=1,
        self_id=999,
        post_type="message",
        message_type="private",
        sub_type="friend",
        message_id=message_id,
        user_id=1001,
        message=message,
        original_message=message,
        raw_message=str(message),
        font=0,
        sender={"user_id": 1001},
    )


def _group(message: Message, message_id: int = 1) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1,
        self_id=999,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=message_id,
        group_id=2001,
        user_id=1001,
        message=message,
        original_message=message,
        raw_message=str(message),
        font=0,
        sender={"user_id": 1001},
    )


def test_exact_dev_and_test_parsing_leaves_plain_messages_untouched():
    protocol = _protocol()
    assert protocol.command_route_hint(Message("/dev build it")) == "dev"
    assert protocol.command_route_hint(Message(" /DEV status")) == "dev"
    assert protocol.command_route_hint(Message("/dev-admin slots")) == "admin"
    assert protocol.command_route_hint(Message("/test hello")) == "staging"
    assert protocol.command_route_hint(Message("/developer")) == "none"
    assert protocol.command_route_hint(Message("hello /dev")) == "none"
    assert protocol.command_route_hint(Message("ordinary private message")) == "none"


def test_command_parsing_does_not_join_text_across_non_text_segments():
    protocol = _protocol()
    split_command = Message(
        [
            MessageSegment.text("/de"),
            MessageSegment.image("https://example.com/a.png"),
            MessageSegment.text("v status"),
        ]
    )
    assert protocol.command_route_hint(split_command) == "none"
    assert protocol.command_route_hint(
        Message([MessageSegment.reply(1), MessageSegment.text("/dev status")])
    ) == "dev"


def test_owner_is_entire_private_or_group_chat():
    protocol = _protocol()
    assert protocol.owner_chat_key(_private(Message("x"))) == "private:1001"
    assert protocol.owner_chat_key(_group(Message("x"))) == "group:2001"


def test_segment_normalization_preserves_text_and_four_bounded_images():
    protocol = _protocol()
    message = Message([MessageSegment.text("hello")])
    for index in range(5):
        message.append(MessageSegment("image", {"url": f"https://example.com/{index}.png", "size": 1024}))
    message.append(MessageSegment("file", {"name": "deferred.zip"}))
    segments, rejected = protocol.normalize_segments(message)
    assert [segment["type"] for segment in segments] == ["text", "image", "image", "image", "image"]
    assert any("four-image" in item for item in rejected)
    assert any("general files" in item for item in rejected)

    oversized = Message([MessageSegment("image", {"url": "https://example.com/a.png", "size": 10 * 1024 * 1024 + 1})])
    segments, rejected = protocol.normalize_segments(oversized)
    assert segments == []
    assert any("10 MiB" in item for item in rejected)


def test_normalized_event_keeps_quote_context_without_implicit_reply_routing():
    protocol = _protocol()
    event = _private(Message([MessageSegment.reply(77), MessageSegment.text("more")]))
    event.reply = SimpleNamespace(
        message_id=77,
        sender={"user_id": 999},
        message=Message([MessageSegment.text("agent question"), MessageSegment.image("https://example.com/q.png")]),
    )
    payload = protocol.normalize_event(event, self_id="999", is_superuser=False)
    assert payload["route_hint"] == "none"
    assert payload["quote"]["message_id"] == "77"
    assert payload["quote"]["text"] == "agent question"
    assert [segment["type"] for segment in payload["quote"]["segments"]] == ["text", "image"]
