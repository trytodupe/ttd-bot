import asyncio
import importlib
import json
import math
import sys
import time
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

    if nonebot.get_plugin("nonebot_plugin_localstore") is None:
        nonebot.load_plugin("nonebot_plugin_localstore")

    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    plugin_dir_text = str(plugin_dir)
    if plugin_dir_text not in sys.path:
        sys.path.insert(0, plugin_dir_text)

    package_name = "tetr_chercher"
    storage_name = "tetr_chercher.user_storage"
    history_name = "tetr_chercher.history_storage"

    if package_name in sys.modules:
        package = importlib.reload(sys.modules[package_name])
    else:
        package = importlib.import_module(package_name)

    if storage_name in sys.modules:
        storage = importlib.reload(sys.modules[storage_name])
    else:
        storage = importlib.import_module(storage_name)

    if history_name in sys.modules:
        history_mod = importlib.reload(sys.modules[history_name])
    else:
        history_mod = importlib.import_module(history_name)

    return package, storage, history_mod


# ── UserStorage ──────────────────────────────────────────────────────────


def test_user_storage_roundtrip(tetr_chercher_modules, tmp_path):
    _, storage, _ = tetr_chercher_modules
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
    _, storage, _ = tetr_chercher_modules
    file_path = tmp_path / "legacy_bindings.json"
    file_path.write_text(
        json.dumps({"users": {"12345": ["trytodupe", "Display"]}}),
        encoding="utf-8",
    )

    reloaded = storage.UserStorage(file_path)
    assert reloaded.get_single_user("12345") == "trytodupe"


# ── HistoryStorage ───────────────────────────────────────────────────────


def test_history_storage_roundtrip(tetr_chercher_modules, tmp_path):
    _, _, history_mod = tetr_chercher_modules
    file_path = tmp_path / "history.json"
    store = history_mod.HistoryStorage(file_path)

    # empty store returns None
    assert store.get_closest_to_24h_ago("key1") is None

    # record a snapshot 25h ago
    t_25h = time.time() - 90000.0
    store.record("key1", {"tr": 100.0, "xp": 5000}, now=t_25h)

    # record a snapshot now
    t_now = time.time()
    store.record("key1", {"tr": 200.0, "xp": 6000}, now=t_now)

    # get_closest_to_24h_ago should find the 25h-ago entry (closest to now-86400)
    prev = store.get_closest_to_24h_ago("key1", now=t_now)
    assert prev is not None
    assert prev["tr"] == 100.0

    # reload from disk, verify persistence
    store2 = history_mod.HistoryStorage(file_path)
    prev2 = store2.get_closest_to_24h_ago("key1", now=t_now)
    assert prev2 is not None
    assert prev2["tr"] == 100.0

    # clear
    store2.clear()
    assert store2.get_closest_to_24h_ago("key1") is None


def test_history_storage_dedup(tetr_chercher_modules, tmp_path):
    _, _, history_mod = tetr_chercher_modules
    store = history_mod.HistoryStorage(tmp_path / "h.json")

    t = time.time()
    store.record("k", {"a": 1}, now=t)
    store.record("k", {"a": 2}, now=t + 5.0)  # within 30s dedup window

    # only one entry
    entries = store._data["k"]
    assert len(entries) == 1
    assert entries[0][1]["a"] == 1


def test_history_storage_trims_old(tetr_chercher_modules, tmp_path):
    _, _, history_mod = tetr_chercher_modules
    store = history_mod.HistoryStorage(tmp_path / "h.json")

    now = time.time()
    # record an entry 72h ago (beyond 48h window)
    store.record("k", {"old": True}, now=now - 259200.0)
    # record a recent entry
    store.record("k", {"new": True}, now=now)

    entries = store._data["k"]
    # old entry should be trimmed
    assert len(entries) == 1
    assert entries[0][1]["new"] is True


# ── Matchers ─────────────────────────────────────────────────────────────


def test_command_matchers_use_command_groups(tetr_chercher_modules):
    module, _, _ = tetr_chercher_modules

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


# ── Bind command ─────────────────────────────────────────────────────────


def test_handle_bind_saves_username(tetr_chercher_modules, tmp_path, monkeypatch):
    module, storage, _ = tetr_chercher_modules
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


# ── Query: no binding ───────────────────────────────────────────────────


def test_handle_query_requires_binding(tetr_chercher_modules, tmp_path, monkeypatch):
    module, storage, _ = tetr_chercher_modules
    module.user_storage = storage.UserStorage(tmp_path / "bindings.json")

    captured: dict[str, str] = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    monkeypatch.setattr(module.query_matcher, "finish", fake_finish)

    asyncio.run(
        module.handle_query(SimpleNamespace(get_user_id=lambda: 12345), module.query_matcher)
    )

    assert captured == {"message": "❌ 请先绑定账号：ttd tetr bind <id>."}


# ── Query: full stats format ─────────────────────────────────────────────

_FAKE_USER_DATA = {
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


def _expected_level(xp: int) -> int:
    if xp <= 0:
        return 1
    return math.floor((xp / 500) ** 0.6 + xp / (5000 + max(0, xp - 4_000_000) / 5000) + 1)


def test_handle_query_formats_bound_user_stats(tetr_chercher_modules, tmp_path, monkeypatch):
    module, storage, history_mod = tetr_chercher_modules
    module.history_storage = history_mod.HistoryStorage(tmp_path / "history.json")
    module.user_storage = storage.UserStorage(tmp_path / "bindings.json")
    module.user_storage.add_user("12345", "trytodupe")

    captured: dict[str, str] = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    async def fake_fetch_user_data(username: str):
        assert username == "trytodupe"
        return _FAKE_USER_DATA.copy()

    monkeypatch.setattr(module.query_matcher, "finish", fake_finish)
    monkeypatch.setattr(module, "fetch_user_data", fake_fetch_user_data)

    asyncio.run(
        module.handle_query(SimpleNamespace(get_user_id=lambda: 12345), module.query_matcher)
    )

    message = captured["message"]
    assert message.startswith("trytodupe的个人信息—TETR.IO")
    assert "123.45" not in message
    assert "1,234.56 TR±3.21, S段" in message
    assert "#1,234" in message
    assert "US #56" in message
    assert f"9,999 Exp ( Lv.{_expected_level(9999)} ) 玩家经验" in message
    assert "12.345s 40L成绩" in message
    assert "6,789 Blitz成绩" in message
    assert "42 ( Lv.7 ) Zen分数" in message
    assert "1 小时 1 分钟 1 秒" in message


# ── Query: 24h diff ──────────────────────────────────────────────────────


def test_handle_query_diff_24h(tetr_chercher_modules, tmp_path, monkeypatch):
    module, storage, history_mod = tetr_chercher_modules
    module.history_storage = history_mod.HistoryStorage(tmp_path / "history.json")
    module.user_storage = storage.UserStorage(tmp_path / "bindings.json")
    module.user_storage.add_user("12345", "trytodupe")

    captured: dict[str, str] = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    async def fake_fetch_user_data(username: str):
        return _FAKE_USER_DATA.copy()

    monkeypatch.setattr(module.query_matcher, "finish", fake_finish)
    monkeypatch.setattr(module, "fetch_user_data", fake_fetch_user_data)

    # record a snapshot from 24h ago with a lower TR
    old_data = _FAKE_USER_DATA.copy()
    old_data["tr"] = 1100.00
    old_data["xp"] = 9000
    old_data["gl_standing"] = 2000
    old_data["playtime"] = 3000
    hist_key = "12345_trytodupe"
    module.history_storage.record(hist_key, old_data, now=time.time() - 86400.0)

    asyncio.run(
        module.handle_query(SimpleNamespace(get_user_id=lambda: 12345), module.query_matcher)
    )

    message = captured["message"]
    # TR diff: 1234.56 - 1100.00 = 134.56
    assert "(↑134.56)" in message
    # XP diff: 9999 - 9000 = 999
    assert "(+999)" in message
    # standing diff: 1234 - 2000 = -766, improvement → (↑766)
    assert "(↑766)" in message
    # playtime diff: 3661 - 3000 = 661
    assert "(+661)" in message


# ── Query: no data (unranked) ────────────────────────────────────────────


def test_handle_query_unranked_shows_placeholder(tetr_chercher_modules, tmp_path, monkeypatch):
    module, storage, history_mod = tetr_chercher_modules
    module.history_storage = history_mod.HistoryStorage(tmp_path / "history.json")
    module.user_storage = storage.UserStorage(tmp_path / "bindings.json")
    module.user_storage.add_user("12345", "newplayer")

    captured: dict[str, str] = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    async def fake_fetch_user_data(username: str):
        return {
            "username": "newplayer",
            "tr": -1,
            "v": 0,
            "rank": "Z",
            "gl_standing": -1,
            "country": "HK",
            "country_rank": -1,
            "sprint": None,
            "blitz": None,
            "zen_score": 0,
            "zen_level": 0,
            "xp": 100,
            "playtime": 0,
        }

    monkeypatch.setattr(module.query_matcher, "finish", fake_finish)
    monkeypatch.setattr(module, "fetch_user_data", fake_fetch_user_data)

    asyncio.run(
        module.handle_query(SimpleNamespace(get_user_id=lambda: 12345), module.query_matcher)
    )

    message = captured["message"]
    assert "暂未进行排位赛" in message
    assert "无40L数据" in message
    assert "无BLITZ数据" in message
    assert "无ZEN数据" in message
    assert f"100 Exp ( Lv.{_expected_level(100)} ) 玩家经验" in message
