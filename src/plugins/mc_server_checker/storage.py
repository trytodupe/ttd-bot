from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import nonebot_plugin_localstore as store


DATA_FILE: Path = store.get_data_file(
    plugin_name="mc_server_checker",
    filename="servers.json",
)


def load_state() -> dict[str, Any]:
    if not DATA_FILE.exists():
        return {"groups": {}}
    try:
        with DATA_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {"groups": {}}
    if not isinstance(data, dict):
        return {"groups": {}}
    groups = data.get("groups")
    if not isinstance(groups, dict):
        data["groups"] = {}
    return data


def save_state(state: dict[str, Any]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=True, indent=2, sort_keys=True)


def get_group_servers(state: dict[str, Any], group_id: int) -> dict[str, Any]:
    groups = state.setdefault("groups", {})
    group_key = str(group_id)
    group = groups.setdefault(group_key, {})
    servers = group.setdefault("servers", {})
    if not isinstance(servers, dict):
        servers = {}
        group["servers"] = servers
    return servers


def get_server_state(state: dict[str, Any], group_id: int, ip: str) -> dict[str, Any]:
    servers = get_group_servers(state, group_id)
    server_state = servers.setdefault(ip, {})
    if not isinstance(server_state, dict):
        server_state = {}
        servers[ip] = server_state
    return server_state


def add_server(state: dict[str, Any], group_id: int, ip: str) -> bool:
    servers = get_group_servers(state, group_id)
    if ip in servers:
        return False
    servers[ip] = {}
    return True


def remove_server(state: dict[str, Any], group_id: int, ip: str) -> bool:
    groups = state.get("groups", {})
    group_key = str(group_id)
    group = groups.get(group_key)
    if not isinstance(group, dict):
        return False
    servers = group.get("servers")
    if not isinstance(servers, dict) or ip not in servers:
        return False
    servers.pop(ip, None)
    if not servers:
        groups.pop(group_key, None)
    return True
