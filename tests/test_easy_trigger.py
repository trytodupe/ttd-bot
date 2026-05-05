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


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (Message("冰茶猫"), True),
        (Message("冰茶喵"), True),
        (Message(" 冰茶猫 "), True),
        (Message("来点冰茶猫"), False),
        (Message([MessageSegment.at(12345), MessageSegment.text("冰茶猫")]), True),
    ],
)
def test_is_ice_tea_neko_trigger(easy_trigger_module, message, expected):
    assert easy_trigger_module._is_ice_tea_neko_trigger(message) is expected


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


def test_is_trigger_allowed_accepts_default(easy_trigger_module):
    event = type("DummyEvent", (), {"user_id": 1001, "group_id": 2001})()

    assert easy_trigger_module._is_trigger_allowed(easy_trigger_module.TRIGGER_MUTE_NOTICE, event) is True


def test_is_trigger_allowed_rejects_user_blacklist(easy_trigger_module, monkeypatch):
    config = easy_trigger_module.Config(
        easy_trigger_user_blacklist={easy_trigger_module.TRIGGER_MUTE_NOTICE: {"1001"}},
    )
    monkeypatch.setattr(easy_trigger_module, "plugin_config", config)
    event = type("DummyEvent", (), {"user_id": 1001, "group_id": 2001})()

    assert easy_trigger_module._is_trigger_allowed(easy_trigger_module.TRIGGER_MUTE_NOTICE, event) is False


def test_is_trigger_allowed_rejects_group_blacklist(easy_trigger_module, monkeypatch):
    config = easy_trigger_module.Config(
        easy_trigger_group_blacklist={easy_trigger_module.TRIGGER_SUPERUSER_PING: {"2001"}},
    )
    monkeypatch.setattr(easy_trigger_module, "plugin_config", config)
    event = type("DummyEvent", (), {"user_id": 1001, "group_id": 2001})()

    assert easy_trigger_module._is_trigger_allowed(easy_trigger_module.TRIGGER_SUPERUSER_PING, event) is False


def test_is_trigger_allowed_whitelist_overrides_blacklist(easy_trigger_module, monkeypatch):
    config = easy_trigger_module.Config(
        easy_trigger_user_blacklist={easy_trigger_module.TRIGGER_SUPERUSER_PING: {"1001"}},
        easy_trigger_group_blacklist={easy_trigger_module.TRIGGER_SUPERUSER_PING: {"2001"}},
        easy_trigger_user_whitelist={easy_trigger_module.TRIGGER_SUPERUSER_PING: {"1001"}},
    )
    monkeypatch.setattr(easy_trigger_module, "plugin_config", config)
    event = type("DummyEvent", (), {"user_id": 1001, "group_id": 2001})()

    assert easy_trigger_module._is_trigger_allowed(easy_trigger_module.TRIGGER_SUPERUSER_PING, event) is True


def test_is_trigger_allowed_keeps_trigger_scopes_separate(easy_trigger_module, monkeypatch):
    config = easy_trigger_module.Config(
        easy_trigger_user_blacklist={easy_trigger_module.TRIGGER_MUTE_NOTICE: {"1001"}},
    )
    monkeypatch.setattr(easy_trigger_module, "plugin_config", config)
    event = type("DummyEvent", (), {"user_id": 1001, "group_id": 2001})()

    assert easy_trigger_module._is_trigger_allowed(easy_trigger_module.TRIGGER_SUPERUSER_PING, event) is True


def test_should_handle_superuser_ping_rejects_trigger_blacklist(easy_trigger_module, monkeypatch):
    config = easy_trigger_module.Config(
        easy_trigger_user_blacklist={easy_trigger_module.TRIGGER_SUPERUSER_PING: {"1001"}},
    )
    monkeypatch.setattr(easy_trigger_module, "plugin_config", config)
    event = type("DummyEvent", (), {"user_id": 1001, "message": Message("ttd")})()

    assert easy_trigger_module._should_handle_superuser_ping(event) is False


def test_should_handle_ice_tea_neko_accepts_keyword(easy_trigger_module):
    event = type("DummyEvent", (), {"message": Message("冰茶喵"), "user_id": 1001})()

    assert easy_trigger_module._should_handle_ice_tea_neko(event) is True


def test_should_handle_ice_tea_neko_rejects_other_text(easy_trigger_module):
    event = type("DummyEvent", (), {"message": Message("冰茶"), "user_id": 1001})()

    assert easy_trigger_module._should_handle_ice_tea_neko(event) is False


def test_config_parses_trigger_id_map_from_json(easy_trigger_module):
    config = easy_trigger_module.Config(
        easy_trigger_user_blacklist='{"superuser_ping": [1001, "1002"], "mute_notice": "2001,2002"}',
    )

    assert config.easy_trigger_user_blacklist == {
        easy_trigger_module.TRIGGER_SUPERUSER_PING: {"1001", "1002"},
        easy_trigger_module.TRIGGER_MUTE_NOTICE: {"2001", "2002"},
    }


@pytest.mark.asyncio
async def test_handle_superuser_ping_replies_for_simple_ping(easy_trigger_module, monkeypatch):
    captured = {}

    async def fake_finish(*, message=None, **kwargs):
        captured["message"] = message

    monkeypatch.setattr(easy_trigger_module.superuser_ping_handler, "finish", fake_finish)

    await easy_trigger_module.handle_superuser_ping()

    assert captured == {"message": "我错了"}


@pytest.mark.asyncio
async def test_handle_ice_tea_neko_replies_with_image(easy_trigger_module, monkeypatch):
    captured = {}

    async def fake_finish(*, message=None, **kwargs):
        captured["message"] = message

    monkeypatch.setattr(easy_trigger_module.ice_tea_neko_handler, "finish", fake_finish)

    await easy_trigger_module.handle_ice_tea_neko()

    assert captured["message"].type == "image"
    assert captured["message"].data["file"].startswith("base64://")
