import importlib
import sys
from pathlib import Path

import nonebot
import pytest


@pytest.fixture(scope="module")
def mc_server_checker_module():
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
    nonebot.load_plugin("nonebot_plugin_apscheduler")

    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    plugin_dir_text = str(plugin_dir)
    if plugin_dir_text not in sys.path:
        sys.path.insert(0, plugin_dir_text)

    module_name = "mc_server_checker"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)

    module._PLAYER_ONLINE_PLAYERS.clear()
    module._PLAYER_LAST_OFFLINE_AT.clear()
    return module


def test_server_change_message_format(mc_server_checker_module):
    module = mc_server_checker_module

    online_result = module.ServerCheckResult(ip="a.example:25565", online=True)
    online_msg = module._format_change_message(
        online_result,
        {"last_seen_online_at": 700.0},
        now=1000.0,
    )
    assert online_msg == "[+] server a.example:25565 | offline for: 5m"

    offline_result = module.ServerCheckResult(ip="a.example:25565", online=False)
    offline_msg = module._format_change_message(
        offline_result,
        {"online_since": 100.0},
        now=1000.0,
    )
    assert offline_msg == "[-] server a.example:25565 | online for: 15m"


def test_player_diff_messages_with_partial_sample(mc_server_checker_module):
    module = mc_server_checker_module
    module._PLAYER_ONLINE_PLAYERS.clear()
    module._PLAYER_LAST_OFFLINE_AT.clear()

    ip = "b.example:25565"
    group_id = 123

    first = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=2,
        player_sample=["Alice", "Bob"],
    )
    assert module._build_player_diff_messages(group_id, first, now=1000.0) == []

    partial = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=2,
        player_sample=["Alice"],
    )
    # Incomplete player sample must not emit leave messages.
    assert module._build_player_diff_messages(group_id, partial, now=1300.0) == []

    full_leave = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=1,
        player_sample=["Alice"],
    )
    leave_messages = module._build_player_diff_messages(group_id, full_leave, now=1600.0)
    assert leave_messages == ["[-] Bob b.example:25565 | online for: 10m"]

    rejoin = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=2,
        player_sample=["Alice", "Bob"],
    )
    join_messages = module._build_player_diff_messages(group_id, rejoin, now=1900.0)
    assert join_messages == ["[+] Bob b.example:25565 | offline for: 5m"]
