from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import nonebot_plugin_localstore as store


DATA_FILE: Path = store.get_data_file(
    plugin_name="mc_server_checker",
    filename="servers.json",
)
PRESETS_FILE: Path = store.get_config_file(
    plugin_name="mc_server_checker",
    filename="presets.json",
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


def _parse_group_ids(value: Any) -> list[int]:
    if value is None:
        return []

    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [part for part in value.replace(",", " ").split() if part]
    else:
        raw_items = [value]

    group_ids: list[int] = []
    seen: set[int] = set()
    for raw_item in raw_items:
        try:
            group_id = int(str(raw_item).strip())
        except (TypeError, ValueError):
            continue
        if group_id in seen:
            continue
        seen.add(group_id)
        group_ids.append(group_id)
    return group_ids


def load_presets() -> dict[str, dict[str, Any]]:
    if not PRESETS_FILE.exists():
        return {}
    try:
        with PRESETS_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}
    raw_presets = data.get("presets")
    if not isinstance(raw_presets, dict):
        return {}

    presets: dict[str, dict[str, Any]] = {}
    for trigger, raw_preset in raw_presets.items():
        if not isinstance(trigger, str):
            continue
        normalized_trigger = trigger.strip()
        if not normalized_trigger or not isinstance(raw_preset, dict):
            continue

        target_ip = str(raw_preset.get("target_ip") or "").strip()
        display_name = str(raw_preset.get("display_name") or normalized_trigger).strip()
        broadcast_group_ids = _parse_group_ids(raw_preset.get("broadcast_group_ids"))
        presets[normalized_trigger] = {
            "target_ip": target_ip,
            "display_name": display_name or normalized_trigger,
            "broadcast_group_ids": broadcast_group_ids,
        }
    return presets


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


def get_preset_state(state: dict[str, Any], trigger: str) -> dict[str, Any]:
    presets = state.setdefault("presets", {})
    if not isinstance(presets, dict):
        presets = {}
        state["presets"] = presets

    preset_key = trigger.strip().casefold()
    preset_state = presets.setdefault(preset_key, {})
    if not isinstance(preset_state, dict):
        preset_state = {}
        presets[preset_key] = preset_state
    return preset_state


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
