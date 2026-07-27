from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageEvent,
    PrivateMessageEvent,
)

RouteHint = Literal["dev", "admin", "staging", "none"]

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], RouteHint], ...] = (
    (re.compile(r"^\s*/dev-admin(?:\s|$)", re.IGNORECASE), "admin"),
    (re.compile(r"^\s*/dev(?:\s|$)", re.IGNORECASE), "dev"),
    (re.compile(r"^\s*/test(?:\s|$)", re.IGNORECASE), "staging"),
)
_PRESERVED_SEGMENTS = frozenset({"text", "image", "reply", "at", "face"})


def owner_chat_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    if isinstance(event, PrivateMessageEvent):
        return f"private:{event.user_id}"
    raise TypeError(f"Unsupported message event: {type(event)!r}")


def command_route_hint(message: Message) -> RouteHint:
    text = ""
    for segment in message:
        if segment.type == "reply":
            continue
        if segment.type != "text":
            return "none"
        text = str(segment.data.get("text", ""))
        break
    for pattern, route in _COMMAND_PATTERNS:
        if pattern.match(text):
            return route
    return "none"


def _safe_segment_data(data: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, str | int | float | bool) or value is None
    }


def _image_size(data: dict[str, Any]) -> int | None:
    for key in ("size", "file_size", "filesize"):
        value = data.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    file_value = data.get("file")
    if isinstance(file_value, str) and file_value.startswith("base64://"):
        encoded = file_value.removeprefix("base64://")
        return (len(encoded) * 3) // 4
    return None


def normalize_segments(message: Message) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    rejected: list[str] = []
    image_count = 0

    for segment in message:
        if segment.type == "file":
            rejected.append("general files are not supported")
            continue
        if segment.type not in _PRESERVED_SEGMENTS:
            continue
        if segment.type == "image":
            image_count += 1
            if image_count > MAX_IMAGES:
                rejected.append(f"image {image_count} exceeds the four-image limit")
                continue
            size = _image_size(segment.data)
            if size is not None and size > MAX_IMAGE_BYTES:
                rejected.append(f"image {image_count} exceeds the 10 MiB limit")
                continue
        normalized.append({"type": segment.type, "data": _safe_segment_data(segment.data)})
    return normalized, rejected


def _normalize_quote(event: MessageEvent) -> dict[str, Any] | None:
    reply = getattr(event, "reply", None)
    if reply is None:
        return None
    reply_message = getattr(reply, "message", None)
    if not isinstance(reply_message, Message):
        try:
            reply_message = Message(reply_message or "")
        except Exception:
            reply_message = Message("")
    segments, rejected = normalize_segments(reply_message)
    return {
        "message_id": str(getattr(reply, "message_id", "") or ""),
        "sender_id": str(getattr(reply, "sender", {}).get("user_id", ""))
        if isinstance(getattr(reply, "sender", None), dict)
        else str(getattr(getattr(reply, "sender", None), "user_id", "") or ""),
        "text": reply_message.extract_plain_text(),
        "segments": segments,
        "attachment_rejections": rejected,
    }


def normalize_event(event: MessageEvent, *, self_id: str, is_superuser: bool) -> dict[str, Any]:
    segments, rejected = normalize_segments(event.message)
    quote = _normalize_quote(event)
    image_count = sum(segment["type"] == "image" for segment in segments)
    if quote:
        limited_quote_segments: list[dict[str, Any]] = []
        for segment in quote["segments"]:
            if segment["type"] == "image":
                image_count += 1
                if image_count > MAX_IMAGES:
                    quote["attachment_rejections"].append("quoted image exceeds the four-image event limit")
                    continue
            limited_quote_segments.append(segment)
        quote["segments"] = limited_quote_segments
    message_id = str(event.message_id)
    return {
        "event_id": f"{owner_chat_key(event)}:{message_id}",
        "owner": owner_chat_key(event),
        "chat_type": "group" if isinstance(event, GroupMessageEvent) else "private",
        "user_id": str(event.user_id),
        "group_id": str(event.group_id) if isinstance(event, GroupMessageEvent) else None,
        "message_id": message_id,
        "bot_id": str(self_id),
        "is_superuser": is_superuser,
        "route_hint": command_route_hint(event.message),
        "text": event.message.extract_plain_text(),
        "segments": segments,
        "quote": quote,
        "attachment_rejections": rejected,
        "timestamp": int(event.time),
    }


@dataclass(slots=True)
class ControllerClient:
    socket_path: str
    timeout_seconds: float = 2.0

    async def request(self, operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = {
            "id": uuid.uuid4().hex,
            "operation": operation,
            "payload": payload or {},
        }

        async def _exchange() -> dict[str, Any]:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
            try:
                writer.write(json.dumps(request, ensure_ascii=False).encode() + b"\n")
                await writer.drain()
                line = await reader.readline()
                if not line:
                    raise ConnectionError("controller closed the socket without a response")
                response = json.loads(line)
                if response.get("id") != request["id"]:
                    raise RuntimeError("controller response id mismatch")
                if not response.get("ok", False):
                    raise RuntimeError(str(response.get("error", "controller request failed")))
                result = response.get("result", {})
                return result if isinstance(result, dict) else {"value": result}
            finally:
                writer.close()
                await writer.wait_closed()

        return await asyncio.wait_for(_exchange(), timeout=self.timeout_seconds)
