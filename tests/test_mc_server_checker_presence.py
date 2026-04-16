import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

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

    for plugin_name in ("nonebot_plugin_localstore", "nonebot_plugin_apscheduler"):
        try:
            nonebot.load_plugin(plugin_name)
        except RuntimeError as exc:
            if "Plugin already exists" not in str(exc):
                raise

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


def test_match_query_trigger_accepts_case_insensitive_hsmc(
    mc_server_checker_module, monkeypatch
):
    module = mc_server_checker_module
    monkeypatch.setattr(
        module,
        "load_presets",
        lambda: {
            "hsmc": {
                "target_ip": "mc.example:25565",
                "display_name": "HSMC",
            }
        },
    )

    assert module._match_query_trigger(SimpleNamespace(get_plaintext=lambda: "hsmc")) is True
    assert module._match_query_trigger(SimpleNamespace(get_plaintext=lambda: "HSMC")) is True
    assert module._match_query_trigger(SimpleNamespace(get_plaintext=lambda: "HsMc")) is True


def test_match_query_trigger_still_requires_exact_keyword(
    mc_server_checker_module, monkeypatch
):
    module = mc_server_checker_module
    monkeypatch.setattr(
        module,
        "load_presets",
        lambda: {
            "hsmc": {
                "target_ip": "mc.example:25565",
                "display_name": "HSMC",
            }
        },
    )

    assert module._match_query_trigger(SimpleNamespace(get_plaintext=lambda: "/hsmc")) is False
    assert module._match_query_trigger(SimpleNamespace(get_plaintext=lambda: " hsmc ")) is True


def test_resolve_query_preset_returns_display_name_and_target(
    mc_server_checker_module, monkeypatch
):
    module = mc_server_checker_module
    monkeypatch.setattr(
        module,
        "load_presets",
        lambda: {
            "hsmc": {
                "target_ip": "mc.example:25565",
                "display_name": "Private HSMC",
                "broadcast_group_ids": [10001, 10002],
            }
        },
    )

    preset = module._resolve_query_preset("HSMC")

    assert preset == module.QueryPreset(
        trigger="hsmc",
        target_ip="mc.example:25565",
        display_name="Private HSMC",
        broadcast_group_ids=(10001, 10002),
    )


def test_get_preset_broadcasts_filters_query_only_presets(
    mc_server_checker_module, monkeypatch
):
    module = mc_server_checker_module
    monkeypatch.setattr(
        module,
        "load_presets",
        lambda: {
            "hsmc": {
                "target_ip": "mc.example:25565",
                "display_name": "Private HSMC",
                "broadcast_group_ids": [10001],
            },
            "queryonly": {
                "target_ip": "query.example:25565",
                "display_name": "Query Only",
                "broadcast_group_ids": [],
            },
        },
    )

    assert module._get_preset_broadcasts() == [
        module.QueryPreset(
            trigger="hsmc",
            target_ip="mc.example:25565",
            display_name="Private HSMC",
            broadcast_group_ids=(10001,),
        )
    ]


def test_send_group_message_uses_first_bot(
    mc_server_checker_module, monkeypatch
):
    module = mc_server_checker_module
    sent_calls: list[tuple[str, dict[str, object]]] = []

    class FirstBot:
        async def call_api(self, api_name: str, **kwargs):
            sent_calls.append((api_name, kwargs))

    class SecondBot:
        async def call_api(self, api_name: str, **kwargs):
            raise AssertionError("second bot should not be used")

    monkeypatch.setattr(
        module,
        "get_bots",
        lambda: {"first": FirstBot(), "second": SecondBot()},
    )

    asyncio.run(module._send_group_message(12345, "hello"))

    assert sent_calls == [
        (
            "send_group_msg",
            {"group_id": 12345, "message": "hello"},
        )
    ]


def test_format_online_result_can_hide_real_ip(mc_server_checker_module):
    module = mc_server_checker_module

    result = module.ServerCheckResult(
        ip="mc.example:25565",
        online=True,
        version="1.21.1",
        motd="Hello",
    )

    formatted = module._format_online_result(
        result,
        {"online_since": 940.0},
        now=1000.0,
        display_name="Private HSMC",
    )

    assert "Server: Private HSMC [1.21.1]" in formatted
    assert "mc.example:25565" not in formatted


def test_run_check_broadcasts_preset_changes_only_to_configured_groups(
    mc_server_checker_module,
    monkeypatch,
):
    module = mc_server_checker_module
    monkeypatch.setattr(
        module,
        "load_presets",
        lambda: {
            "hsmc": {
                "target_ip": "mc.example:25565",
                "display_name": "Private HSMC",
                "broadcast_group_ids": [10001, 10002],
            }
        },
    )

    state = {
        "groups": {},
        "presets": {
            "hsmc": {
                "last_status": "offline",
                "last_seen_online_at": 700.0,
                "consecutive_failures": 0,
            }
        },
    }
    sent_messages: list[tuple[int, str]] = []

    async def fake_check_server(ip: str):
        assert ip == "mc.example:25565"
        return module.ServerCheckResult(ip=ip, online=True)

    async def fake_send_group_message(group_id: int, message: str):
        sent_messages.append((group_id, message))

    monkeypatch.setattr(module, "_check_server", fake_check_server)
    monkeypatch.setattr(module, "_send_group_message", fake_send_group_message)
    monkeypatch.setattr(module, "load_state", lambda: state)
    monkeypatch.setattr(module, "save_state", lambda current_state: None)
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)

    asyncio.run(
        module._run_check(
            send_changes=True,
            include_player_changes=False,
            only_online_servers=False,
        )
    )

    assert sent_messages == [
        (10001, "[+] server Private HSMC | offline for: 5m"),
        (10002, "[+] server Private HSMC | offline for: 5m"),
    ]


def test_handle_status_broadcasts_preset_change_to_configured_groups(
    mc_server_checker_module,
    monkeypatch,
):
    module = mc_server_checker_module
    monkeypatch.setattr(
        module,
        "load_presets",
        lambda: {
            "hsmc": {
                "target_ip": "mc.example:25565",
                "display_name": "Private HSMC",
                "broadcast_group_ids": [10001, 10002],
            }
        },
    )

    state = {
        "groups": {},
        "presets": {
            "hsmc": {
                "last_status": "offline",
                "last_seen_online_at": 700.0,
                "consecutive_failures": 0,
            }
        },
    }
    sent_messages: list[tuple[int, str]] = []
    finished_messages: list[str] = []

    async def fake_check_server(ip: str):
        assert ip == "mc.example:25565"
        return module.ServerCheckResult(ip=ip, online=True)

    async def fake_send_group_message(group_id: int, message: str):
        sent_messages.append((group_id, message))

    async def fake_finish(message: str) -> None:
        finished_messages.append(message)

    monkeypatch.setattr(module, "_check_server", fake_check_server)
    monkeypatch.setattr(module, "_send_group_message", fake_send_group_message)
    monkeypatch.setattr(module, "load_state", lambda: state)
    monkeypatch.setattr(module, "save_state", lambda current_state: None)
    monkeypatch.setattr(module.time, "time", lambda: 1000.0)
    monkeypatch.setattr(module.status_matcher, "finish", fake_finish)

    asyncio.run(
        module.handle_status(
            SimpleNamespace(group_id=999, user_id=1, get_plaintext=lambda: "hsmc")
        )
    )

    assert sent_messages == [
        (10001, "[+] server Private HSMC | offline for: 5m"),
        (10002, "[+] server Private HSMC | offline for: 5m"),
    ]
    assert finished_messages
    assert "Status changes:" in finished_messages[0]


def test_run_check_broadcasts_preset_player_changes_to_configured_groups(
    mc_server_checker_module,
    monkeypatch,
):
    module = mc_server_checker_module
    module._PLAYER_ONLINE_PLAYERS.clear()
    module._PLAYER_LAST_OFFLINE_AT.clear()

    monkeypatch.setattr(
        module,
        "load_presets",
        lambda: {
            "hsmc": {
                "target_ip": "mc.example:25565",
                "display_name": "Private HSMC",
                "broadcast_group_ids": [10001, 10002],
            }
        },
    )

    state = {
        "groups": {},
        "presets": {
            "hsmc": {
                "last_status": "offline",
                "last_seen_online_at": 700.0,
                "consecutive_failures": 0,
            }
        },
    }
    sent_messages: list[tuple[int, str]] = []
    call_count = 0
    times = iter([1000.0, 1300.0])

    async def fake_check_server(ip: str):
        nonlocal call_count
        call_count += 1
        assert ip == "mc.example:25565"
        if call_count == 1:
            return module.ServerCheckResult(
                ip=ip,
                online=True,
                players_online=2,
                player_sample=["Alice", "Bob"],
            )
        if call_count == 2:
            return module.ServerCheckResult(
                ip=ip,
                online=True,
                players_online=1,
                player_sample=["Alice"],
            )
        raise AssertionError("unexpected server check call")

    async def fake_send_group_message(group_id: int, message: str):
        sent_messages.append((group_id, message))

    monkeypatch.setattr(module, "_check_server", fake_check_server)
    monkeypatch.setattr(module, "_send_group_message", fake_send_group_message)
    monkeypatch.setattr(module, "load_state", lambda: state)
    monkeypatch.setattr(module, "save_state", lambda current_state: None)
    monkeypatch.setattr(module.time, "time", lambda: next(times))

    asyncio.run(
        module._run_check(
            send_changes=True,
            include_player_changes=True,
            only_online_servers=True,
        )
    )
    asyncio.run(
        module._run_check(
            send_changes=True,
            include_player_changes=True,
            only_online_servers=True,
        )
    )

    assert sent_messages == [
        (10001, "[+] server Private HSMC | offline for: 5m"),
        (10002, "[+] server Private HSMC | offline for: 5m"),
        (10001, "[-] Bob Private HSMC | online for: 5m"),
        (10002, "[-] Bob Private HSMC | online for: 5m"),
    ]


def test_player_diff_messages_with_partial_sample(mc_server_checker_module):
    module = mc_server_checker_module
    module._PLAYER_ONLINE_PLAYERS.clear()
    module._PLAYER_LAST_OFFLINE_AT.clear()
    module._PLAYER_PENDING_JOINS.clear()
    module._PLAYER_PENDING_LEAVES.clear()

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
    assert leave_messages == []

    leave_messages = module._build_player_diff_messages(group_id, full_leave, now=1660.0)
    assert leave_messages == ["[-] Bob b.example:25565 | online for: 10m"]

    rejoin = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=2,
        player_sample=["Alice", "Bob"],
    )
    join_messages = module._build_player_diff_messages(group_id, rejoin, now=1900.0)
    assert join_messages == []

    join_messages = module._build_player_diff_messages(group_id, rejoin, now=1960.0)
    assert join_messages == ["[+] Bob b.example:25565 | offline for: 5m"]


def test_player_diff_messages_ignore_names_with_spaces(mc_server_checker_module):
    module = mc_server_checker_module
    module._PLAYER_ONLINE_PLAYERS.clear()
    module._PLAYER_LAST_OFFLINE_AT.clear()
    module._PLAYER_PENDING_JOINS.clear()
    module._PLAYER_PENDING_LEAVES.clear()

    ip = "c.example:25565"
    group_id = 456

    first = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=2,
        player_sample=["Alice", "Anonymous Player"],
    )
    assert module._build_player_diff_messages(group_id, first, now=1000.0) == []
    assert module._PLAYER_ONLINE_PLAYERS[(group_id, ip)] == {"Alice": 1000.0}

    leave = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=0,
        player_sample=[],
    )
    assert module._build_player_diff_messages(group_id, leave, now=1059.0) == []
    assert module._build_player_diff_messages(group_id, leave, now=1060.0) == [
        "[-] Alice c.example:25565 | online for: 1m"
    ]


def test_format_online_result_ignores_names_with_spaces(mc_server_checker_module):
    module = mc_server_checker_module

    result = module.ServerCheckResult(
        ip="mc.example:25565",
        online=True,
        version="1.21.1",
        motd="Hello",
        players_online=2,
        players_max=20,
        player_sample=["Alice", "Anonymous Player"],
    )

    formatted = module._format_online_result(
        result,
        {"online_since": 940.0},
        now=1000.0,
    )

    assert "Online players: Alice" in formatted
    assert "Anonymous Player" not in formatted


def test_player_diff_messages_require_debounce_for_join_and_leave(
    mc_server_checker_module,
):
    module = mc_server_checker_module
    module._PLAYER_ONLINE_PLAYERS.clear()
    module._PLAYER_LAST_OFFLINE_AT.clear()
    module._PLAYER_PENDING_JOINS.clear()
    module._PLAYER_PENDING_LEAVES.clear()

    ip = "d.example:25565"
    group_id = 789

    first = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=1,
        player_sample=["Alice"],
    )
    assert module._build_player_diff_messages(group_id, first, now=1000.0) == []

    leave = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=0,
        player_sample=[],
    )
    assert module._build_player_diff_messages(group_id, leave, now=1030.0) == []
    assert module._build_player_diff_messages(group_id, leave, now=1089.0) == []
    assert module._build_player_diff_messages(group_id, leave, now=1090.0) == [
        "[-] Alice d.example:25565 | online for: 30s"
    ]

    rejoin = module.ServerCheckResult(
        ip=ip,
        online=True,
        players_online=1,
        player_sample=["Alice"],
    )
    assert module._build_player_diff_messages(group_id, rejoin, now=1120.0) == []
    assert module._build_player_diff_messages(group_id, rejoin, now=1149.0) == []
    assert module._build_player_diff_messages(group_id, rejoin, now=1150.0) == [
        "[+] Alice d.example:25565 | offline for: 30s"
    ]


def test_collect_group_servers_only_online(mc_server_checker_module):
    module = mc_server_checker_module
    state = {
        "groups": {
            "100": {
                "servers": {
                    "a.example:25565": {"last_status": "online"},
                    "b.example:25565": {"last_status": "offline"},
                }
            },
            "200": {
                "servers": {
                    "c.example:25565": {"last_status": "online"},
                }
            },
        }
    }

    all_servers = module._collect_group_servers(state, only_online_servers=False)
    assert all_servers == {
        100: ["a.example:25565", "b.example:25565"],
        200: ["c.example:25565"],
    }

    online_only = module._collect_group_servers(state, only_online_servers=True)
    assert online_only == {
        100: ["a.example:25565"],
        200: ["c.example:25565"],
    }


def test_apply_status_update_requires_consecutive_failures_to_mark_offline(
    mc_server_checker_module,
):
    module = mc_server_checker_module
    server_state = {
        "last_status": "online",
        "online_since": 100.0,
        "last_seen_online_at": 900.0,
        "consecutive_failures": 0,
    }
    offline_result = module.ServerCheckResult(
        ip="a.example:25565",
        online=False,
        error="timeout",
    )

    for attempt in range(1, 5):
        message = module._apply_status_update(
            group_id=123,
            server_state=server_state,
            result=offline_result,
            now=1000.0 + attempt,
        )
        assert message is None
        assert server_state["last_status"] == "online"
        assert server_state["consecutive_failures"] == attempt

    message = module._apply_status_update(
        group_id=123,
        server_state=server_state,
        result=offline_result,
        now=1005.0,
    )
    assert message == "[-] server a.example:25565 | online for: 15m5s"
    assert server_state["last_status"] == "offline"
    assert server_state["consecutive_failures"] == 5


def test_apply_status_update_resets_failures_after_success(mc_server_checker_module):
    module = mc_server_checker_module
    server_state = {
        "last_status": "online",
        "online_since": 100.0,
        "last_seen_online_at": 900.0,
        "consecutive_failures": 4,
        "last_error": "timeout",
    }
    online_result = module.ServerCheckResult(ip="a.example:25565", online=True)

    message = module._apply_status_update(
        group_id=123,
        server_state=server_state,
        result=online_result,
        now=1000.0,
    )

    assert message is None
    assert server_state["last_status"] == "online"
    assert server_state["consecutive_failures"] == 0
    assert server_state["last_error"] is None
