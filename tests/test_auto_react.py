from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Adapter
from nonebot.plugin import get_plugin

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PLUGIN_DIR = PROJECT_ROOT / "src" / "plugins"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from src.plugins._reaction_catalog import (  # noqa: E402
    ENABLED_RANDOM_REACTIONS,
    TYPE_1_REACTIONS,
)


@pytest.fixture(scope="module")
def auto_react_module():
    try:
        driver = nonebot.get_driver()
    except ValueError:
        nonebot.init(superusers={"12345"})
        driver = nonebot.get_driver()

    try:
        driver.register_adapter(Adapter)
    except ValueError:
        pass

    if get_plugin("nonebot_plugin_localstore") is None:
        nonebot.load_plugin("nonebot_plugin_localstore")

    module_name = "auto_react"
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


@pytest.mark.asyncio
async def test_handle_uses_only_the_shared_type_1_pool(
    auto_react_module,
    monkeypatch,
) -> None:
    module = auto_react_module
    selected = next(
        reaction
        for reaction in TYPE_1_REACTIONS
        if reaction.reaction_id == "193"
    )
    calls: list[tuple[str, dict]] = []

    class FakeBot:
        async def call_api(self, api: str, **data) -> None:
            calls.append((api, data))

    def choose(population):
        assert population is ENABLED_RANDOM_REACTIONS
        return selected

    monkeypatch.setattr(module.random, "choice", choose)

    await module.handle(FakeBot(), SimpleNamespace(message_id=123))

    assert calls == [
        (
            "set_msg_emoji_like",
            {"message_id": 123, "emoji_id": "193"},
        )
    ]
