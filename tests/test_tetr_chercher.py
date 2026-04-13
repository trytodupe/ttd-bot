import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest


@pytest.fixture(scope="module")
def tetr_chercher_modules():
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

    nonebot.load_plugin("nonebot_plugin_localstore")

    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    plugin_dir_text = str(plugin_dir)
    if plugin_dir_text not in sys.path:
        sys.path.insert(0, plugin_dir_text)

    package_name = "tetr_chercher"
    storage_name = "tetr_chercher.user_storage"

    if package_name in sys.modules:
        package = importlib.reload(sys.modules[package_name])
    else:
        package = importlib.import_module(package_name)

    if storage_name in sys.modules:
        storage = importlib.reload(sys.modules[storage_name])
    else:
        storage = importlib.import_module(storage_name)

    package.history_data.clear()
    return package, storage


def test_user_storage_roundtrip(tetr_chercher_modules, tmp_path):
    _, storage = tetr_chercher_modules
    file_path = tmp_path / "user_bindings.json"

    store = storage.UserStorage(file_path)
    assert store.get_all_users() == {}

    assert store.add_user("12345", "trytodupe") is True
    assert store.has_user("12345") is True
    assert store.get_single_user("12345") == "trytodupe"

    persisted = json.loads(file_path.read_text(encoding="utf-8"))
    assert persisted == {
        "users": {
            "12345": "trytodupe",
        }
    }

    reloaded = storage.UserStorage(file_path)
    assert reloaded.get_single_user("12345") == "trytodupe"


def test_user_storage_loads_legacy_binding_format(tetr_chercher_modules, tmp_path):
    _, storage = tetr_chercher_modules
    file_path = tmp_path / "legacy_bindings.json"
    file_path.write_text(
        json.dumps({"users": {"12345": ["trytodupe", "Display"]}}),
        encoding="utf-8",
    )

    reloaded = storage.UserStorage(file_path)
    assert reloaded.get_single_user("12345") == "trytodupe"


def test_command_matchers_use_command_groups(tetr_chercher_modules):
    module, _ = tetr_chercher_modules

    def command_sets(matcher):
        sets = []
        for checker in matcher.rule.checkers:
            call = getattr(checker, "call", None)
            cmds = getattr(call, "cmds", None)
            if cmds is not None:
                sets.append({tuple(cmd) for cmd in cmds})
        return sets

    query_commands = command_sets(module.query_matcher)[0]
    assert ("tetr",) in query_commands
    assert ("TETR",) in query_commands
    assert ("tetR",) in query_commands
    assert ("TtD", "tEtR") in query_commands
    assert command_sets(module.bind_cmd) == [
        {
            ("tetr", "bind "),
        }
    ]


def test_handle_bind_saves_username(tetr_chercher_modules, tmp_path, monkeypatch):
    module, storage = tetr_chercher_modules
    module.user_storage = storage.UserStorage(tmp_path / "bindings.json")

    captured: dict[str, str] = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    monkeypatch.setattr(module.bind_cmd, "finish", fake_finish)

    asyncio.run(
        module._handle_bind(
            SimpleNamespace(get_user_id=lambda: 12345),
            SimpleNamespace(extract_plain_text=lambda: "trytodupe"),
        )
    )

    assert captured == {"message": "✅ 绑定成功！"}
    assert module.user_storage.get_single_user("12345") == "trytodupe"


def test_handle_query_requires_binding(tetr_chercher_modules, tmp_path, monkeypatch):
    module, storage = tetr_chercher_modules
    module.user_storage = storage.UserStorage(tmp_path / "bindings.json")

    captured: dict[str, str] = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    monkeypatch.setattr(module.query_matcher, "finish", fake_finish)

    asyncio.run(
        module.handle_query(SimpleNamespace(get_user_id=lambda: 12345), module.query_matcher)
    )

    assert captured == {"message": "❌ 请先绑定账号：ttd tetr bind <id>."}


def test_handle_query_formats_bound_user_stats(tetr_chercher_modules, tmp_path, monkeypatch):
    module, storage = tetr_chercher_modules
    module.history_data.clear()
    module.user_storage = storage.UserStorage(tmp_path / "bindings.json")
    module.user_storage.add_user("12345", "trytodupe")

    captured: dict[str, str] = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    async def fake_fetch_user_data(username: str):
        assert username == "trytodupe"
        return {
            "username": "trytodupe",
            "tr": 1234.56,
            "v": 3.21,
            "rank": "S",
            "gl_standing": 1234,
            "country": "US",
            "country_rank": 56,
            "sprint": 12.345,
            "blitz": 6789,
            "zen_score": 42,
            "zen_level": 7,
            "xp": 9999,
            "playtime": 3661,
        }

    monkeypatch.setattr(module.query_matcher, "finish", fake_finish)
    monkeypatch.setattr(module, "fetch_user_data", fake_fetch_user_data)

    asyncio.run(
        module.handle_query(SimpleNamespace(get_user_id=lambda: 12345), module.query_matcher)
    )

    message = captured["message"]
    assert message.startswith("trytodupe的个人信息—TETR.IO US")
    assert "123.45" not in message
    assert "1,234.56 TR±3.21, S段" in message
    assert "#1,234 全球排名" in message
    assert "US #56" in message
    assert "9,999 Exp 玩家总经验" in message
    assert "12.345s 40L成绩" in message
    assert "6,789 Blitz成绩" in message
    assert "42 Zen分数 (Lv.7)" in message
    assert "1小时 1分钟 1秒" in message
