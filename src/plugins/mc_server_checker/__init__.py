from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, cast

from mcstatus import JavaServer
from nonebot import (
    CommandGroup,
    get_bots,
    get_driver,
    get_plugin_config,
    on_message,
    require,
)
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, is_type, to_me
from nonebot.params import CommandArg

from .config import Config
from .storage import (
    add_server,
    get_group_servers,
    get_server_state,
    load_state,
    remove_server,
    save_state,
)

require("nonebot_plugin_localstore")
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


__plugin_meta__ = PluginMetadata(
    name="mc-server-checker",
    description="Check Java server status per group and notify on changes.",
    usage="Trigger by sending specific keywords; admins can add/remove servers.",
    config=Config,
)

plugin_config = get_plugin_config(Config)
_POLL_INTERVAL_SECONDS = max(1, int(plugin_config.mc_server_checker_interval_seconds))
_PLAYER_POLL_INTERVAL_SECONDS = max(
    1, int(plugin_config.mc_server_checker_player_poll_interval_seconds)
)

_STATE_LOCK = asyncio.Lock()
_POLL_LOCK = asyncio.Lock()

_PLAYER_ONLINE_PLAYERS: dict[tuple[int, str], dict[str, float]] = {}
_PLAYER_LAST_OFFLINE_AT: dict[tuple[int, str], dict[str, float]] = {}

_QUERY_TRIGGERS = {
    "\u4fe1\u606f",
    "\u798f",
}

_HEX_FORMATTING_RE = re.compile("\u00a7x(?:\u00a7[0-9A-Fa-f]){6}")
_FORMATTING_CODE_RE = re.compile("\u00a7[0-9A-FK-ORa-fk-or]")


@dataclass
class ServerCheckResult:
    ip: str
    online: bool
    version: str | None = None
    motd: str | None = None
    players_online: int = 0
    players_max: int = 0
    player_sample: list[str] = field(default_factory=list)
    ping_ms: int | None = None
    error: str | None = None


def _match_query_trigger(event: GroupMessageEvent) -> bool:
    text = event.get_plaintext().strip()
    return text in _QUERY_TRIGGERS


def _is_admin(user_id: int) -> bool:
    return int(user_id) in set(plugin_config.mc_server_checker_admins)


def _compact_text(text: Any) -> str:
    if text is None:
        return ""
    return " ".join(str(text).replace("\r", " ").replace("\n", " ").split())


def _strip_minecraft_formatting(text: str) -> str:
    if not text:
        return ""
    without_hex = _HEX_FORMATTING_RE.sub("", text)
    without_codes = _FORMATTING_CODE_RE.sub("", without_hex)
    return without_codes.replace("\u00a7", "")


def _format_motd(description: Any) -> str:
    if description is None:
        return ""
    if isinstance(description, str):
        return _strip_minecraft_formatting(_compact_text(description))
    try:
        plain = description.to_plain()
    except Exception:
        plain = str(description)
    return _strip_minecraft_formatting(_compact_text(plain))


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = int(max(0, seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return "".join(parts)


def _format_online_result(
    result: ServerCheckResult, server_state: dict[str, Any], now: float
) -> str:
    online_since = server_state.get("online_since")
    uptime = (
        _format_duration(now - float(online_since))
        if online_since is not None
        else "unknown"
    )
    ping = f"{result.ping_ms}ms" if result.ping_ms is not None else "unknown"
    version = result.version or "unknown"
    motd = result.motd or "unknown"
    players = f"{result.players_online}/{result.players_max}"
    sample = ", ".join(result.player_sample) if result.player_sample else "愣着干嘛, 上号啊"
    return (
        f"Server: {result.ip} [{version}]\n"
        f"Players: {players} | Ping: {ping} | Uptime: {uptime}\n"
        f"MOTD: {motd}\n"
        f"Online players: {sample}"
    )


def _format_offline_result(
    result: ServerCheckResult, server_state: dict[str, Any], now: float
) -> str:
    last_seen = server_state.get("last_seen_online_at")
    last_seen_text = (
        _format_duration(now - float(last_seen)) if last_seen is not None else "unknown"
    )
    error = result.error or "unknown"
    return (
        f"Server: {result.ip}\n"
        f"Status: offline | Last seen: {last_seen_text}\n"
        f"Error: {error}"
    )


def _format_change_message(
    result: ServerCheckResult, server_state: dict[str, Any], now: float
) -> str:
    if result.online:
        last_seen = server_state.get("last_seen_online_at")
        last_seen_text = (
            _format_duration(now - float(last_seen))
            if last_seen is not None
            else "unknown"
        )
        return f"[+] server {result.ip} | offline for: {last_seen_text}"
    online_since = server_state.get("online_since")
    uptime_text = (
        _format_duration(now - float(online_since))
        if online_since is not None
        else "unknown"
    )
    return f"[-] server {result.ip} | online for: {uptime_text}"


def _presence_key(group_id: int, ip: str) -> tuple[int, str]:
    return (group_id, ip)


def _clear_player_presence(group_id: int, ip: str) -> None:
    key = _presence_key(group_id, ip)
    _PLAYER_ONLINE_PLAYERS.pop(key, None)
    _PLAYER_LAST_OFFLINE_AT.pop(key, None)


def _mark_all_players_offline(group_id: int, ip: str, now: float) -> None:
    key = _presence_key(group_id, ip)
    previous = _PLAYER_ONLINE_PLAYERS.pop(key, None)
    if not previous:
        return
    offline_map = _PLAYER_LAST_OFFLINE_AT.setdefault(key, {})
    for player_name in previous.keys():
        offline_map[player_name] = now


def _build_player_diff_messages(
    group_id: int, result: ServerCheckResult, now: float
) -> list[str]:
    if not result.online:
        _mark_all_players_offline(group_id, result.ip, now)
        return []

    key = _presence_key(group_id, result.ip)
    sample_players = {
        name.strip()
        for name in result.player_sample
        if isinstance(name, str) and name.strip()
    }

    if key not in _PLAYER_ONLINE_PLAYERS:
        _PLAYER_ONLINE_PLAYERS[key] = {name: now for name in sample_players}
        _PLAYER_LAST_OFFLINE_AT.setdefault(key, {})
        return []

    previous_online = _PLAYER_ONLINE_PLAYERS.get(key, {})
    offline_map = _PLAYER_LAST_OFFLINE_AT.setdefault(key, {})
    messages: list[str] = []
    next_online: dict[str, float] = {}

    for player_name in sorted(sample_players):
        if player_name in previous_online:
            next_online[player_name] = previous_online[player_name]
            continue

        next_online[player_name] = now
        offline_at = offline_map.get(player_name)
        offline_for = (
            _format_duration(now - float(offline_at))
            if offline_at is not None
            else "unknown"
        )
        messages.append(
            f"[+] {player_name} {result.ip} | offline for: {offline_for}"
        )

    full_sample = len(sample_players) == result.players_online
    for player_name, online_since in previous_online.items():
        if player_name in sample_players:
            continue
        if not full_sample:
            next_online[player_name] = online_since
            continue

        online_for = _format_duration(now - float(online_since))
        messages.append(f"[-] {player_name} {result.ip} | online for: {online_for}")
        offline_map[player_name] = now

    _PLAYER_ONLINE_PLAYERS[key] = next_online
    return messages


async def _check_server(ip: str) -> ServerCheckResult:
    timeout = max(1, int(plugin_config.mc_server_checker_timeout_seconds))
    try:
        server = await JavaServer.async_lookup(ip, timeout=timeout)
        status = await asyncio.wait_for(server.async_status(), timeout=timeout)
        version = getattr(status.version, "name", None) if status.version else None
        motd = _format_motd(getattr(status, "description", None))
        players_online = int(getattr(status.players, "online", 0) or 0)
        players_max = int(getattr(status.players, "max", 0) or 0)
        sample = []
        if status.players and status.players.sample:
            for player in status.players.sample:
                name = getattr(player, "name", None)
                if name:
                    sample.append(str(name))
        ping_ms = None
        if hasattr(status, "latency") and status.latency is not None:
            ping_ms = int(round(float(status.latency)))
        return ServerCheckResult(
            ip=ip,
            online=True,
            version=version,
            motd=motd,
            players_online=players_online,
            players_max=players_max,
            player_sample=sample,
            ping_ms=ping_ms,
        )
    except Exception as exc:
        return ServerCheckResult(
            ip=ip,
            online=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def _apply_status_update(
    group_id: int,
    server_state: dict[str, Any],
    result: ServerCheckResult,
    now: float,
) -> str | None:
    prev_status = server_state.get("last_status", "unknown")
    change_message: str | None = None
    if result.online:
        if prev_status != "online":
            server_state["online_since"] = now
            change_message = _format_change_message(result, server_state, now)
        if server_state.get("online_since") is None:
            server_state["online_since"] = now
        server_state["last_status"] = "online"
        server_state["last_seen_online_at"] = now
        server_state["last_error"] = None
    else:
        if prev_status == "online":
            server_state["last_seen_online_at"] = now
            change_message = _format_change_message(result, server_state, now)
            _mark_all_players_offline(group_id, result.ip, now)
        server_state["last_status"] = "offline"
        server_state["last_error"] = result.error
    server_state["last_check_at"] = now
    return change_message


def _select_bot() -> Bot | None:
    bots = get_bots()
    if not bots:
        return None
    return cast(Bot, next(iter(bots.values())))


async def _send_group_message(group_id: int, message: str) -> None:
    bot = _select_bot()
    if bot is None:
        logger.warning("No available bot to send group message.")
        return
    try:
        await bot.call_api("send_group_msg", group_id=group_id, message=message)
    except Exception as exc:
        logger.warning(f"Failed to send group message: {exc}")


def _collect_group_servers(
    state: dict[str, Any], only_online_servers: bool
) -> dict[int, list[str]]:
    groups = state.get("groups", {})
    group_servers: dict[int, list[str]] = {}
    for group_id_str, group_data in groups.items():
        if not isinstance(group_data, dict):
            continue
        servers = group_data.get("servers", {})
        if not isinstance(servers, dict):
            continue
        try:
            group_id = int(group_id_str)
        except ValueError:
            continue

        ips: list[str] = []
        for ip, server_state in servers.items():
            if not isinstance(ip, str):
                continue
            if not only_online_servers:
                ips.append(ip)
                continue
            if isinstance(server_state, dict) and server_state.get("last_status") == "online":
                ips.append(ip)

        if ips:
            group_servers[group_id] = ips
    return group_servers


async def _run_check(
    send_changes: bool,
    include_player_changes: bool = False,
    only_online_servers: bool = False,
) -> None:
    async with _POLL_LOCK:
        async with _STATE_LOCK:
            state = load_state()
            group_servers = _collect_group_servers(state, only_online_servers)

        if not group_servers:
            return

        results_by_group: dict[int, list[ServerCheckResult]] = {}
        for group_id, servers in group_servers.items():
            tasks = [_check_server(ip) for ip in servers]
            results_by_group[group_id] = await asyncio.gather(*tasks)

        now = time.time()
        change_messages: dict[int, list[str]] = {}

        async with _STATE_LOCK:
            state = load_state()
            for group_id, results in results_by_group.items():
                for result in results:
                    server_state = get_server_state(state, group_id, result.ip)
                    change_message = _apply_status_update(
                        group_id, server_state, result, now
                    )
                    if change_message:
                        change_messages.setdefault(group_id, []).append(change_message)
                    if include_player_changes and result.online:
                        player_messages = _build_player_diff_messages(group_id, result, now)
                        if player_messages:
                            change_messages.setdefault(group_id, []).extend(player_messages)
            save_state(state)

        if send_changes:
            for group_id, messages in change_messages.items():
                if messages:
                    await _send_group_message(group_id, "\n".join(messages))


_JOB_ID = "mc_server_checker_poll"
_PLAYER_JOB_ID = "mc_server_checker_player_poll"
driver = get_driver()


@driver.on_startup
async def _start_polling() -> None:
    if scheduler.get_job(_JOB_ID):
        pass
    else:
        scheduler.add_job(
            _run_check,
            "interval",
            seconds=_POLL_INTERVAL_SECONDS,
            id=_JOB_ID,
            coalesce=True,
            misfire_grace_time=30,
            kwargs={
                "send_changes": True,
                "include_player_changes": False,
                "only_online_servers": False,
            },
        )

    if scheduler.get_job(_PLAYER_JOB_ID):
        return
    scheduler.add_job(
        _run_check,
        "interval",
        seconds=_PLAYER_POLL_INTERVAL_SECONDS,
        id=_PLAYER_JOB_ID,
        coalesce=True,
        misfire_grace_time=15,
        kwargs={
            "send_changes": True,
            "include_player_changes": True,
            "only_online_servers": True,
        },
    )


@driver.on_shutdown
async def _stop_polling() -> None:
    job = scheduler.get_job(_JOB_ID)
    if job:
        job.remove()
    player_job = scheduler.get_job(_PLAYER_JOB_ID)
    if player_job:
        player_job.remove()


command_rule = is_type(GroupMessageEvent) & to_me()
mc_cmd_group = CommandGroup("mc", rule=command_rule, priority=10, block=True)
mc_add_cmd = mc_cmd_group.command("add")
mc_remove_cmd = mc_cmd_group.command("remove")


@mc_add_cmd.handle()
async def handle_add(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    if not _is_admin(int(event.user_id)):
        await mc_add_cmd.finish("Permission denied.")

    ip = args.extract_plain_text().strip()
    if not ip:
        await mc_add_cmd.finish("Usage: ttd mc add <ip>")

    async with _STATE_LOCK:
        state = load_state()
        added = add_server(state, int(event.group_id), ip)
        if added:
            save_state(state)
            response = f"Server added: {ip}"
        else:
            response = f"Server already exists: {ip}"

    await mc_add_cmd.finish(response)


@mc_remove_cmd.handle()
async def handle_remove(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    if not _is_admin(int(event.user_id)):
        await mc_remove_cmd.finish("Permission denied.")

    ip = args.extract_plain_text().strip()
    if not ip:
        await mc_remove_cmd.finish("Usage: ttd mc remove <ip>")

    async with _STATE_LOCK:
        state = load_state()
        removed = remove_server(state, int(event.group_id), ip)
        if removed:
            save_state(state)
            _clear_player_presence(int(event.group_id), ip)
            response = f"Server removed: {ip}"
        else:
            response = f"Server not found: {ip}"

    await mc_remove_cmd.finish(response)


status_matcher = on_message(
    rule=is_type(GroupMessageEvent) & Rule(_match_query_trigger),
    priority=20,
    block=True,
)


@status_matcher.handle()
async def handle_status(event: GroupMessageEvent) -> None:
    group_id = int(event.group_id)

    async with _STATE_LOCK:
        state = load_state()
        servers = list(get_group_servers(state, group_id).keys())

    if not servers:
        await status_matcher.finish("No servers configured for this group.")

    results = await asyncio.gather(*[_check_server(ip) for ip in servers])
    now = time.time()
    change_messages: list[str] = []
    blocks: list[str] = []

    async with _STATE_LOCK:
        state = load_state()
        for result in results:
            server_state = get_server_state(state, group_id, result.ip)
            change_message = _apply_status_update(group_id, server_state, result, now)
            if change_message:
                change_messages.append(change_message)
            if result.online:
                blocks.append(_format_online_result(result, server_state, now))
            else:
                blocks.append(_format_offline_result(result, server_state, now))
        save_state(state)

    message = "\n===\n".join(blocks)
    if change_messages:
        message = "Status changes:\n" + "\n".join(change_messages) + "\n\n" + message

    await status_matcher.finish(message)
