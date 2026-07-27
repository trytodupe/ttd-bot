from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, WSMsgType, web

SLOT = int(os.environ["TTD_SLOT"])
LISTEN_PORT = int(os.environ["TTD_PROXY_PORT"])
RUNTIME_PORT = int(os.environ["TTD_RUNTIME_PORT"])
STATE_DIR = Path("/run/ttd-dev-agent")
CONFIG_PATH = STATE_DIR / f"slot-{SLOT}.json"
DEV_COMMAND = re.compile(r"^\s*/dev(?:-admin)?(?:\s|$)", re.IGNORECASE)
TEST_COMMAND = re.compile(r"^\s*/test(?:\s|$)", re.IGNORECASE)
POLICY_MAX_AGE_SECONDS = 120


def config() -> dict[str, Any]:
    try:
        value = json.loads(CONFIG_PATH.read_text())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def command_text_from_message(message: Any) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, list):
        return ""
    for item in message:
        if item.get("type") == "reply":
            continue
        if item.get("type") != "text":
            return ""
        return str(item.get("data", {}).get("text", ""))
    return ""


def strip_test(message: Any) -> Any:
    if isinstance(message, str):
        return TEST_COMMAND.sub("", message, count=1)
    if not isinstance(message, list):
        return message
    result = [dict(item) for item in message]
    for item in result:
        if item.get("type") != "text":
            continue
        data = dict(item.get("data", {}))
        current = str(data.get("text", ""))
        updated = TEST_COMMAND.sub("", current, count=1)
        data["text"] = updated
        item["data"] = data
        if updated != current:
            break
    return result


def event_owner(payload: dict[str, Any]) -> str | None:
    if payload.get("message_type") == "group" and payload.get("group_id") is not None:
        return f"group:{payload['group_id']}"
    if payload.get("message_type") == "private" and payload.get("user_id") is not None:
        return f"private:{payload['user_id']}"
    return None


def allow_event(payload: dict[str, Any]) -> bool:
    cfg = config()
    if event_owner(payload) != cfg.get("owner"):
        return False
    inbound = cfg.get("inbound", {})
    if (
        not inbound.get("enabled", False)
        or time.time() - float(cfg.get("heartbeat", 0)) > POLICY_MAX_AGE_SECONDS
    ):
        return False
    if str(payload.get("user_id", "")) in {str(value) for value in inbound.get("user_deny", [])}:
        return False
    text = command_text_from_message(payload.get("message"))
    if DEV_COMMAND.match(text):
        return False
    if not TEST_COMMAND.match(text):
        return False
    payload["message"] = strip_test(payload.get("message"))
    payload["raw_message"] = TEST_COMMAND.sub("", str(payload.get("raw_message", "")), count=1)
    return True


def action_destination(payload: dict[str, Any]) -> tuple[str, str] | None:
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    if params.get("group_id") is not None:
        return "group", str(params["group_id"])
    if params.get("user_id") is not None:
        return "user", str(params["user_id"])
    return None


def allow_action(payload: dict[str, Any]) -> bool:
    destination = action_destination(payload)
    if destination is None:
        return True
    kind, identifier = destination
    rules = config().get("outbound", {})
    denied = {str(value) for value in rules.get(f"{kind}_deny", [])}
    allowed = {str(value) for value in rules.get(f"{kind}_allow", [])}
    if identifier in denied:
        return False
    return not allowed or identifier in allowed


async def websocket(request: web.Request) -> web.StreamResponse:
    upstream = web.WebSocketResponse(heartbeat=30)
    await upstream.prepare(request)
    if request.headers.get("X-TTD-Health-Probe") == "1":
        await upstream.close()
        return upstream
    pending_events: list[tuple[float, dict[str, Any]]] = []
    async with ClientSession() as session:
        while not upstream.closed:
            try:
                downstream = await session.ws_connect(
                    f"http://127.0.0.1:{RUNTIME_PORT}/onebot/v11/ws",
                    headers={
                        "X-Self-ID": request.headers.get("X-Self-ID", "0"),
                        "X-Client-Role": "Universal",
                    },
                    heartbeat=30,
                )
            except Exception:
                # Keep the stable SnowLuma connection open while no staging
                # release is active. Consume and drop traffic fail-closed, then
                # retry the isolated runtime without forcing client reconnects.
                try:
                    message = await upstream.receive(timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    break
                if message.type == WSMsgType.TEXT:
                    payload = json.loads(message.data)
                    if payload.get("post_type") and allow_event(payload):
                        now = time.time()
                        pending_events = [
                            item for item in pending_events if now - item[0] <= 60
                        ]
                        pending_events.append((now, payload))
                        pending_events = pending_events[-20:]
                continue

            now = time.time()
            for queued_at, payload in pending_events:
                if now - queued_at <= 60:
                    await downstream.send_json(payload)
            pending_events.clear()

            async def from_upstream() -> None:
                async for msg in upstream:
                    if msg.type != WSMsgType.TEXT:
                        continue
                    payload = json.loads(msg.data)
                    if payload.get("post_type"):
                        if allow_event(payload):
                            await downstream.send_json(payload)
                    else:
                        await downstream.send_json(payload)

            async def from_downstream() -> None:
                async for msg in downstream:
                    if msg.type != WSMsgType.TEXT:
                        continue
                    payload = json.loads(msg.data)
                    if not allow_action(payload):
                        await downstream.send_json({
                            "status": "failed",
                            "retcode": 1403,
                            "message": "blocked by staging outbound policy",
                            "echo": payload.get("echo"),
                        })
                        continue
                    await upstream.send_json(payload)

            tasks = [
                asyncio.create_task(from_upstream()),
                asyncio.create_task(from_downstream()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await downstream.close()
            for task in done:
                task.exception()
    return upstream


def main() -> None:
    app = web.Application(client_max_size=12 * 1024 * 1024)
    app.router.add_get("/{tail:.*}", websocket)
    web.run_app(app, host="127.0.0.1", port=LISTEN_PORT, access_log=None)


if __name__ == "__main__":
    main()
