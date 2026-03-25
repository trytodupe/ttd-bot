import importlib
import sys
from pathlib import Path

import nonebot
import pytest


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

    return importlib.import_module("easy_trigger")


@pytest.mark.parametrize(
    ("plain_text", "expected"),
    [
        ("", True),
        ("   ", True),
        ("ping", False),
        (" ping ", False),
    ],
)
def test_is_simple_ping(easy_trigger_module, plain_text, expected):
    assert easy_trigger_module._is_simple_ping(plain_text) is expected


@pytest.mark.asyncio
async def test_handle_superuser_ping_replies_for_simple_ping(easy_trigger_module, monkeypatch):
    captured = {}

    async def fake_finish(*, message=None, **kwargs):
        captured["message"] = message

    monkeypatch.setattr(easy_trigger_module.superuser_ping_handler, "finish", fake_finish)

    class DummyEvent:
        def get_plaintext(self) -> str:
            return "   "

    await easy_trigger_module.handle_superuser_ping(DummyEvent())

    assert captured == {"message": "\u6211\u9519\u4e86"}


@pytest.mark.asyncio
async def test_handle_superuser_ping_ignores_meaningful_text(easy_trigger_module, monkeypatch):
    called = False

    async def fake_finish(*, message=None, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(easy_trigger_module.superuser_ping_handler, "finish", fake_finish)

    class DummyEvent:
        def get_plaintext(self) -> str:
            return "status"

    await easy_trigger_module.handle_superuser_ping(DummyEvent())

    assert called is False
