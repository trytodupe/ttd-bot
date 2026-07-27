from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from nonebot import Bot, get_driver, get_plugin_config, logger, on_message
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.rule import Rule
from nonebot.typing import T_State

from .config import Config
from .protocol import ControllerClient, command_route_hint, normalize_event

plugin_config = get_plugin_config(Config)
driver = get_driver()
client = ControllerClient(
    plugin_config.dev_agent_socket_path,
    plugin_config.dev_agent_socket_timeout_seconds,
)
_pollers: dict[str, asyncio.Task[None]] = {}


def _is_superuser(event: MessageEvent) -> bool:
    return str(event.user_id) in {str(item) for item in driver.config.superusers}


def _text_message(value: Any) -> Message:
    if isinstance(value, list):
        segments: list[MessageSegment] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            segment_type = str(item.get("type", "text"))
            data = item.get("data", {})
            if isinstance(data, dict):
                segments.append(MessageSegment(segment_type, data))
        return Message(segments)
    return Message(str(value or ""))


async def _send_outbox_message(bot: Bot, item: dict[str, Any]) -> str:
    message = _text_message(item.get("message"))
    if item.get("chat_type") == "group":
        result = await bot.call_api(
            "send_group_msg",
            group_id=int(item["destination_id"]),
            message=message,
        )
    else:
        result = await bot.call_api(
            "send_private_msg",
            user_id=int(item["destination_id"]),
            message=message,
        )
    if isinstance(result, dict):
        return str(result.get("message_id", ""))
    return str(getattr(result, "message_id", "") or "")


async def _outbox_loop(bot: Bot) -> None:
    while True:
        try:
            result = await client.request("outbox.poll", {"bot_id": str(bot.self_id), "limit": 20})
            items = result.get("items", [])
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                try:
                    message_id = await _send_outbox_message(bot, item)
                except Exception as exc:
                    logger.warning("Development agent outbox delivery failed: %r", exc)
                    await client.request(
                        "outbox.nack",
                        {"outbox_id": item.get("id"), "error": str(exc)[:500], "bot_id": str(bot.self_id)},
                    )
                    continue
                await client.request(
                    "outbox.ack",
                    {"outbox_id": item.get("id"), "message_id": message_id, "bot_id": str(bot.self_id)},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Development agent controller is unavailable: %r", exc)
        await asyncio.sleep(plugin_config.dev_agent_outbox_poll_seconds)


if plugin_config.dev_agent_enabled:

    async def _route(event: MessageEvent, bot: Bot, state: T_State) -> bool:
        payload = normalize_event(event, self_id=str(bot.self_id), is_superuser=_is_superuser(event))
        hint = command_route_hint(event.message)
        try:
            result = await client.request("inbound.route", payload)
        except Exception as exc:
            if hint in {"dev", "admin", "staging"}:
                state["dev_agent_gateway_error"] = str(exc)
                return True
            return False
        state["dev_agent_gateway_result"] = result
        return result.get("route") in {"dev", "admin", "staging"}


    # This internal slash-command gateway intentionally accepts bare /dev and
    # /test in allowlisted groups; owner ACLs provide its addressing boundary.
    gateway_matcher = on_message(rule=Rule(_route), priority=1, block=True)


    @gateway_matcher.handle()
    async def _handle_gateway(state: T_State) -> None:
        error = state.get("dev_agent_gateway_error")
        if error:
            await gateway_matcher.finish("开发环境当前不可用，请稍后重试。")
        result = state.get("dev_agent_gateway_result", {})
        immediate = result.get("immediate") if isinstance(result, dict) else None
        if immediate:
            await gateway_matcher.finish(_text_message(immediate))


    @driver.on_bot_connect
    async def _start_outbox_poller(bot: Bot) -> None:
        old_task = _pollers.pop(str(bot.self_id), None)
        if old_task:
            old_task.cancel()
        _pollers[str(bot.self_id)] = asyncio.create_task(_outbox_loop(bot))


    @driver.on_bot_disconnect
    async def _stop_outbox_poller(bot: Bot) -> None:
        task = _pollers.pop(str(bot.self_id), None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
