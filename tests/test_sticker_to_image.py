import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
import pytest


@pytest.fixture(scope="module")
def sticker_to_image_module():
    try:
        driver = nonebot.get_driver()
    except ValueError:
        nonebot.init(superusers={"12345"})
        driver = nonebot.get_driver()

    from nonebot.adapters.onebot.v11 import Adapter

    try:
        driver.register_adapter(Adapter)
    except ValueError:
        pass

    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    plugin_dir_text = str(plugin_dir)
    if plugin_dir_text not in sys.path:
        sys.path.insert(0, plugin_dir_text)

    module_name = "sticker_to_image"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)

    return module


def _image_segment(*, sub_type: str, url: str = "https://example.com/a.jpg", file: str = "a.jpg"):
    segment = MessageSegment.image(url)
    segment.data["sub_type"] = sub_type
    segment.data["url"] = url
    segment.data["file"] = file
    return segment


def test_should_handle_private_event(sticker_to_image_module):
    module = sticker_to_image_module
    event = SimpleNamespace(message_type="private", to_me=False)

    assert module._should_handle_event(event) is True


def test_should_handle_group_event_only_when_to_me(sticker_to_image_module):
    module = sticker_to_image_module

    assert module._should_handle_event(SimpleNamespace(message_type="group", to_me=True)) is True
    assert module._should_handle_event(SimpleNamespace(message_type="group", to_me=False)) is False


def test_extract_sticker_source_matches_sub_type_one(sticker_to_image_module):
    module = sticker_to_image_module
    message = Message([
        _image_segment(sub_type="0", url="https://example.com/normal.jpg"),
        _image_segment(sub_type="1", url="https://example.com/sticker.jpg"),
    ])

    assert module._extract_sticker_source(message) == "https://example.com/sticker.jpg"


def test_extract_sticker_source_falls_back_to_file(sticker_to_image_module):
    module = sticker_to_image_module
    sticker = _image_segment(sub_type="1", url="", file="EC10E2D3B0EE98584AB13DD2E80BA028.jpg")
    message = Message([sticker])

    assert module._extract_sticker_source(message) == "EC10E2D3B0EE98584AB13DD2E80BA028.jpg"


def test_extract_sticker_source_ignores_normal_image(sticker_to_image_module):
    module = sticker_to_image_module
    message = Message([_image_segment(sub_type="0")])

    assert module._extract_sticker_source(message) is None


def test_build_image_reply_creates_plain_image_segment(sticker_to_image_module):
    module = sticker_to_image_module

    reply = module._build_image_reply("https://example.com/sticker.jpg")

    assert reply.type == "image"
    assert reply.data["file"] == "https://example.com/sticker.jpg"
    assert "sub_type" not in reply.data


def test_extract_control_text_ignores_at_and_image(sticker_to_image_module):
    module = sticker_to_image_module
    message = Message(
        [
            MessageSegment.at(12345),
            MessageSegment.text("  url  "),
            _image_segment(sub_type="0"),
        ]
    )

    assert module._extract_control_text(message) == "url"


def test_extract_reply_sticker_source_requires_to_me_and_simple_text(sticker_to_image_module):
    module = sticker_to_image_module
    reply_message = Message([_image_segment(sub_type="1", url="https://example.com/replied.jpg")])
    event = SimpleNamespace(
        to_me=True,
        message=Message([MessageSegment.at(12345), MessageSegment.text(" ")]),
        reply=SimpleNamespace(message=reply_message),
    )

    assert module._extract_reply_sticker_source(event) == "https://example.com/replied.jpg"


def test_extract_reply_sticker_source_allows_url_control_text(sticker_to_image_module):
    module = sticker_to_image_module
    reply_message = Message([_image_segment(sub_type="1", url="https://example.com/replied.jpg")])
    event = SimpleNamespace(
        to_me=True,
        message=Message([MessageSegment.at(12345), MessageSegment.text(" url ")]),
        reply=SimpleNamespace(message=reply_message),
    )

    assert module._extract_reply_sticker_source(event) == "https://example.com/replied.jpg"


def test_extract_reply_sticker_source_ignores_meaningful_text(sticker_to_image_module):
    module = sticker_to_image_module
    reply_message = Message([_image_segment(sub_type="1", url="https://example.com/replied.jpg")])
    event = SimpleNamespace(
        to_me=True,
        message=Message([MessageSegment.at(12345), MessageSegment.text("pls")]),
        reply=SimpleNamespace(message=reply_message),
    )

    assert module._extract_reply_sticker_source(event) is None


@pytest.mark.asyncio
async def test_handle_sticker_replies_with_reply_url_text(sticker_to_image_module, monkeypatch):
    module = sticker_to_image_module
    captured = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    monkeypatch.setattr(module.matcher, "finish", fake_finish)

    event = SimpleNamespace(
        message_type="group",
        to_me=True,
        message=Message([MessageSegment.at(12345), MessageSegment.text(" url ")]),
        reply=SimpleNamespace(
            message=Message([_image_segment(sub_type="1", url="https://example.com/replied.jpg")])
        ),
    )

    await module.handle_sticker(event)

    assert captured == {"message": "https://example.com/replied.jpg"}


@pytest.mark.asyncio
async def test_handle_sticker_ignores_non_matching_event(sticker_to_image_module, monkeypatch):
    module = sticker_to_image_module
    called = False

    async def fake_finish(message=None, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(module.matcher, "finish", fake_finish)

    event = SimpleNamespace(
        message_type="group",
        to_me=False,
        message=Message([_image_segment(sub_type="1", url="https://example.com/sticker.jpg")]),
        reply=None,
    )

    await module.handle_sticker(event)

    assert called is False
