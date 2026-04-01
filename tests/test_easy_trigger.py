import importlib
import sys
from pathlib import Path

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment


@pytest.fixture(scope="module")
def easy_trigger_module():
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

    module_name = "easy_trigger"
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (Message(""), True),
        (Message("   "), True),
        (Message([MessageSegment.at(12345), MessageSegment.text(" ")]), True),
        (Message("ping"), False),
        (Message([MessageSegment.image("https://example.com/image.jpg")]), False),
    ],
)
def test_is_simple_ping(easy_trigger_module, message, expected):
    assert easy_trigger_module._is_simple_ping(message) is expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (Message("ttd"), True),
        (Message("hello TTD"), True),
        (Message([MessageSegment.at(12345), MessageSegment.text(" ttd help ")]), True),
        (Message("tid"), False),
        (Message([MessageSegment.image("https://example.com/image.jpg")]), False),
    ],
)
def test_contains_superuser_ping_keyword(easy_trigger_module, message, expected):
    assert easy_trigger_module._contains_superuser_ping_keyword(message) is expected


def test_should_handle_superuser_ping_accepts_simple_ping(easy_trigger_module):
    event = type("DummyEvent", (), {"message": Message("   ")})()

    assert easy_trigger_module._should_handle_superuser_ping(event) is True


def test_should_handle_superuser_ping_accepts_keyword(easy_trigger_module):
    event = type("DummyEvent", (), {"message": Message("hello ttd")})()

    assert easy_trigger_module._should_handle_superuser_ping(event) is True


def test_should_handle_superuser_ping_rejects_other_meaningful_text(easy_trigger_module):
    event = type("DummyEvent", (), {"message": Message("hello bot")})()

    assert easy_trigger_module._should_handle_superuser_ping(event) is False


def test_should_handle_superuser_ping_rejects_reply_even_for_simple_ping(easy_trigger_module):
    event = type(
        "DummyEvent",
        (),
        {"message": Message(""), "reply": object()},
    )()

    assert easy_trigger_module._should_handle_superuser_ping(event) is False


def test_should_handle_superuser_ping_rejects_reply_even_for_keyword(easy_trigger_module):
    event = type(
        "DummyEvent",
        (),
        {"message": Message("ttd"), "reply": object()},
    )()

    assert easy_trigger_module._should_handle_superuser_ping(event) is False


@pytest.mark.asyncio
async def test_handle_superuser_ping_replies_for_simple_ping(easy_trigger_module, monkeypatch):
    captured = {}

    async def fake_finish(*, message=None, **kwargs):
        captured["message"] = message

    monkeypatch.setattr(easy_trigger_module.superuser_ping_handler, "finish", fake_finish)

    await easy_trigger_module.handle_superuser_ping()

    assert captured == {"message": "我错了"}
