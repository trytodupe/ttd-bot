from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(slots=True)
class AttachmentSource:
    index: int
    kind: str
    name: str
    url: str
    declared_size: int | None = None


@dataclass(slots=True)
class DownloadedAttachment:
    source: AttachmentSource
    path: Path
    size: int
    sha256: str
    mime_type: str


@dataclass(slots=True)
class ForwardBundle:
    transcript: str
    attachments: list[AttachmentSource]


def _segments(message: Any) -> list[dict[str, Any]]:
    try:
        normalized: list[dict[str, Any]] = []
        for segment in message:
            if isinstance(segment, dict):
                normalized.append(segment)
            else:
                normalized.append({"type": segment.type, "data": dict(segment.data)})
        return normalized
    except (TypeError, AttributeError, ValueError):
        return []


def _safe_filename(value: str, fallback: str) -> str:
    name = Path(value).name.strip() or fallback
    cleaned = _SAFE_NAME.sub("_", name).strip("._")
    return (cleaned or fallback)[:180]


def parse_forward_messages(messages: list[dict[str, Any]]) -> ForwardBundle:
    lines: list[str] = []
    attachments: list[AttachmentSource] = []
    attachment_index = 0
    for node_index, node in enumerate(messages, 1):
        sender = node.get("sender") if isinstance(node.get("sender"), dict) else {}
        sender_name = str(
            sender.get("card")
            or sender.get("nickname")
            or node.get("user_id")
            or "未知用户"
        )
        timestamp = node.get("time")
        header = f"## {node_index}. {sender_name}"
        if timestamp:
            header += f" ({timestamp})"
        lines.append(header)
        content: list[str] = []
        for segment in _segments(node.get("message")):
            segment_type = str(segment.get("type") or "")
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
            if segment_type == "text":
                text = str(data.get("text") or "").strip()
                if text:
                    content.append(text)
                continue
            if segment_type not in {"image", "video", "record", "file"}:
                if segment_type and segment_type not in {"reply", "at"}:
                    content.append(f"[{segment_type}]")
                continue
            url = str(data.get("url") or "").strip()
            if not url:
                content.append(f"[{segment_type}: 无可用下载地址]")
                continue
            attachment_index += 1
            raw_name = str(data.get("name") or data.get("file") or "")
            guessed_suffix = {
                "image": ".jpg",
                "video": ".mp4",
                "record": ".mp3",
                "file": ".bin",
            }[segment_type]
            fallback = f"attachment-{attachment_index}{guessed_suffix}"
            name = _safe_filename(raw_name, fallback)
            try:
                declared_size = (
                    int(data.get("size") or data.get("file_size") or 0) or None
                )
            except (TypeError, ValueError):
                declared_size = None
            attachments.append(
                AttachmentSource(
                    attachment_index, segment_type, name, url, declared_size
                )
            )
            content.append(f"[@附件{attachment_index}: {name}]")
        lines.append("\n".join(content) if content else "[空消息]")
        lines.append("")
    return ForwardBundle("\n".join(lines).strip(), attachments)


def unpack_forward_response(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        result = result.get("messages")
    if not isinstance(result, list):
        raise TypeError("get_forward_msg returned no message list")
    return [item for item in result if isinstance(item, dict)]


async def fetch_forward_messages(
    bot: Any, forward_id: str, max_depth: int
) -> list[dict[str, Any]]:
    async def fetch(current_id: str, depth: int) -> list[dict[str, Any]]:
        result = await bot.call_api("get_forward_msg", id=current_id)
        nodes = unpack_forward_response(result)
        flattened: list[dict[str, Any]] = []
        for node in nodes:
            regular_segments: list[dict[str, Any]] = []
            nested_ids: list[str] = []
            for segment in _segments(node.get("message")):
                data = (
                    segment.get("data") if isinstance(segment.get("data"), dict) else {}
                )
                if segment.get("type") == "forward" and data.get("id"):
                    nested_ids.append(str(data["id"]))
                else:
                    regular_segments.append(segment)
            if regular_segments:
                flattened.append({**node, "message": regular_segments})
            for nested_id in nested_ids:
                if depth >= max_depth:
                    flattened.append(
                        {
                            **node,
                            "message": [
                                {
                                    "type": "text",
                                    "data": {"text": "[嵌套聊天记录超过深度限制]"},
                                }
                            ],
                        }
                    )
                else:
                    flattened.extend(await fetch(nested_id, depth + 1))
        return flattened

    return await fetch(forward_id, 0)


async def download_attachment(
    client: httpx.AsyncClient,
    source: AttachmentSource,
    target_dir: Path,
    *,
    max_bytes: int,
) -> DownloadedAttachment:
    if source.declared_size is not None and source.declared_size > max_bytes:
        raise ValueError(f"{source.name} exceeds the per-file size limit")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source.index:03d}-{source.name}"
    digest = hashlib.sha256()
    total = 0
    mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    async with client.stream("GET", source.url, follow_redirects=True) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type:
            mime_type = content_type
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                response_size = int(content_length)
            except ValueError:
                response_size = None
            if response_size is not None and response_size > max_bytes:
                raise ValueError(f"{source.name} exceeds the per-file size limit")
        with target.open("wb") as handle:
            async for chunk in response.aiter_bytes(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"{source.name} exceeds the per-file size limit")
                digest.update(chunk)
                handle.write(chunk)
    if total == 0:
        raise ValueError(f"{source.name} is empty")
    return DownloadedAttachment(source, target, total, digest.hexdigest(), mime_type)
