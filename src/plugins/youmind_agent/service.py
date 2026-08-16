from __future__ import annotations

import asyncio
import inspect
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

from .client import YouMindAPIError, YouMindClient
from .config import Config
from .forward_bundle import (
    DownloadedAttachment,
    download_attachment,
    fetch_forward_messages,
    parse_forward_messages,
)
from .results import TurnResult, media_from_download, parse_turn
from .storage import StateStore, project_key


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def send_message_id(result: Any) -> int | None:
    value = (
        result.get("message_id")
        if isinstance(result, dict)
        else getattr(result, "message_id", None)
    )
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def forward_id_from_message(message: Any) -> str:
    try:
        for segment in message:
            if segment.type == "forward" and segment.data.get("id"):
                return str(segment.data["id"])
    except (TypeError, AttributeError):
        pass
    return ""


class YouMindService:
    def __init__(self, config: Config, store: StateStore, cache_dir: Path):
        self.config = config
        self.store = store
        self.cache_dir = cache_dir
        self._project_locks: dict[str, asyncio.Lock] = {}
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def client(self) -> YouMindClient:
        return YouMindClient(
            self.config.youmind_api_key,
            base_url=self.config.youmind_base_url,
            proxy=self.config.youmind_proxy,
            request_timeout=self.config.youmind_chat_timeout_seconds,
        )

    def launch(self, local_id: str, coroutine: Any) -> bool:
        existing = self._tasks.get(local_id)
        if existing is not None and not existing.done():
            if inspect.iscoroutine(coroutine):
                coroutine.close()
            return False
        task = asyncio.create_task(coroutine)
        self._tasks[local_id] = task

        def done(completed: asyncio.Task[None]) -> None:
            if self._tasks.get(local_id) is completed:
                self._tasks.pop(local_id, None)
            if (
                not completed.cancelled()
                and (error := completed.exception()) is not None
            ):
                logger.error("YouMind task %s crashed: %r", local_id, error)

        task.add_done_callback(done)
        return True

    async def create_pending_chat(
        self,
        *,
        group_id: int,
        user_id: int,
        request_message_id: int,
        forward_message_id: int,
        instruction: str,
    ) -> dict[str, Any]:
        local_id = uuid.uuid4().hex
        chat = {
            "local_id": local_id,
            "group_id": group_id,
            "user_id": user_id,
            "request_message_id": request_message_id,
            "forward_message_id": forward_message_id,
            "instruction": instruction,
            "board_id": "",
            "chat_id": "",
            "status": "preparing",
            "pending_questions": [],
            "last_assistant_id": "",
            "error": "",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        await self.store.put_chat(chat)
        return chat

    async def run_start(self, bot: Bot, chat: dict[str, Any], forward_id: str) -> None:
        try:
            project = await self._ensure_project(bot, chat, forward_id)
            chat["board_id"] = project["board_id"]
            chat["status"] = "running"
            chat["updated_at"] = utc_now()
            await self.store.put_chat(chat)
            references = [
                {"type": "file", "id": item["id"], "name": item.get("name", "")}
                for item in project.get("files", [])
                if isinstance(item, dict) and item.get("id")
            ]
            prompt = self._agent_prompt(chat["instruction"], chat["local_id"])
            async with self.client() as client:
                payload = {
                    "boardId": project["board_id"],
                    "message": prompt,
                    "atReferences": references,
                }
                try:
                    response = await client.call("createChat", payload)
                except YouMindAPIError as exc:
                    if exc.status_code != 0:
                        raise
                    response = {}
                chat_id = (
                    str(response.get("id") or "") if isinstance(response, dict) else ""
                )
                if not chat_id:
                    chat_id = await self._recover_chat_id(
                        client, project["board_id"], chat["local_id"]
                    )
                if not chat_id:
                    raise RuntimeError("YouMind did not return a chat ID")
                chat["chat_id"] = chat_id
                chat["updated_at"] = utc_now()
                await self.store.put_chat(chat)
                turn = await self._wait_for_turn(client, chat_id, "")
                await self._deliver_turn(bot, chat, turn)
        except Exception as exc:  # noqa: BLE001 - task boundary must persist every failure
            await self._fail(bot, chat, exc)

    async def continue_chat(
        self, bot: Bot, chat: dict[str, Any], user_message_id: int, answer: str
    ) -> None:
        local_id = str(chat["local_id"])
        lock = self._chat_locks.setdefault(local_id, asyncio.Lock())
        async with lock:
            current = await self.store.get_chat(local_id)
            await self._continue_chat_locked(
                bot,
                current or chat,
                user_message_id,
                answer,
            )

    async def _continue_chat_locked(
        self, bot: Bot, chat: dict[str, Any], user_message_id: int, answer: str
    ) -> None:
        if chat.get("status") in {"preparing", "uploading", "running"}:
            await self._send_text(
                bot, chat, user_message_id, "这个 YouMind 请求仍在处理中。"
            )
            return
        if chat.get("status") == "cancelled":
            await self._send_text(
                bot, chat, user_message_id, "这个 YouMind 请求已取消。"
            )
            return
        if not chat.get("chat_id"):
            await self._send_text(
                bot, chat, user_message_id, "这个请求还没有可继续的 YouMind Chat。"
            )
            return
        previous_assistant_id = str(chat.get("last_assistant_id") or "")
        questions = chat.get("pending_questions")
        message = answer
        if isinstance(questions, list) and questions:
            message = f"Q: {questions[0]}\nA: {answer}"
        chat["status"] = "running"
        chat["pending_questions"] = []
        chat["updated_at"] = utc_now()
        await self.store.put_chat(chat)
        try:
            async with self.client() as client:
                try:
                    await client.call(
                        "sendMessage",
                        {
                            "boardId": chat["board_id"],
                            "chatId": chat["chat_id"],
                            "message": message,
                        },
                    )
                except YouMindAPIError as exc:
                    if exc.status_code != 0:
                        raise
                turn = await self._wait_for_turn(
                    client, chat["chat_id"], previous_assistant_id
                )
                await self._deliver_turn(bot, chat, turn, reply_to=user_message_id)
        except Exception as exc:  # noqa: BLE001 - task boundary must persist every failure
            await self._fail(bot, chat, exc, reply_to=user_message_id)

    async def _ensure_project(
        self, bot: Bot, chat: dict[str, Any], forward_id: str
    ) -> dict[str, Any]:
        group_id = int(chat["group_id"])
        forward_message_id = int(chat["forward_message_id"])
        key = project_key(group_id, forward_message_id)
        lock = self._project_locks.setdefault(key, asyncio.Lock())
        async with lock:
            existing = await self.store.get_project(group_id, forward_message_id)
            if existing and existing.get("status") == "ready":
                return existing
            chat["status"] = "uploading"
            chat["updated_at"] = utc_now()
            await self.store.put_chat(chat)
            nodes = await fetch_forward_messages(
                bot, forward_id, self.config.youmind_max_forward_depth
            )
            bundle = parse_forward_messages(nodes)
            if len(bundle.attachments) > self.config.youmind_max_files:
                raise ValueError(
                    f"合并转发包含 {len(bundle.attachments)} 个附件，超过上限 {self.config.youmind_max_files}"
                )
            declared_total = sum(item.declared_size or 0 for item in bundle.attachments)
            if declared_total > self.config.youmind_max_total_bytes:
                raise ValueError("合并转发附件总大小超过限制")

            title = f"QQ-{group_id}-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}-{chat['user_id']}"
            async with self.client() as client:
                if existing and existing.get("board_id"):
                    project = existing
                else:
                    board = await client.create_board(title)
                    project = {
                        "group_id": group_id,
                        "forward_message_id": forward_message_id,
                        "board_id": str(board["id"]),
                        "title": title,
                        "status": "uploading",
                        "files": [],
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                    await self.store.put_project(project)

                files = (
                    project.get("files")
                    if isinstance(project.get("files"), list)
                    else []
                )
                if not any(
                    isinstance(item, dict) and item.get("kind") == "transcript"
                    for item in files
                ):
                    document = await client.create_document(
                        project["board_id"],
                        "QQ 合并转发记录",
                        bundle.transcript or "[聊天记录没有可提取的文本]",
                    )
                    files.append(
                        {
                            "id": str(document["id"]),
                            "name": "QQ 合并转发记录",
                            "kind": "transcript",
                        }
                    )
                    project["files"] = files
                    await self.store.put_project(project)

                work_dir = self.cache_dir / chat["local_id"]
                try:
                    downloaded = await self._download_all(bundle.attachments, work_dir)
                    known_hashes = {
                        str(item.get("sha256"))
                        for item in files
                        if isinstance(item, dict) and item.get("sha256")
                    }
                    semaphore = asyncio.Semaphore(
                        self.config.youmind_upload_concurrency
                    )

                    async def upload(
                        item: DownloadedAttachment,
                    ) -> dict[str, Any] | None:
                        if item.sha256 in known_hashes:
                            return None
                        async with semaphore:
                            result = await client.upload_file(
                                board_id=project["board_id"],
                                path=item.path,
                                title=item.source.name,
                                sha256=item.sha256,
                                mime_type=item.mime_type,
                            )
                        return {
                            "id": str(result["id"]),
                            "name": item.source.name,
                            "kind": item.source.kind,
                            "sha256": item.sha256,
                        }

                    uploaded = await asyncio.gather(
                        *(upload(item) for item in downloaded),
                        return_exceptions=True,
                    )
                    files.extend(item for item in uploaded if isinstance(item, dict))
                    project["files"] = files
                    project["updated_at"] = utc_now()
                    await self.store.put_project(project)
                    upload_error = next(
                        (item for item in uploaded if isinstance(item, BaseException)),
                        None,
                    )
                    if upload_error is not None:
                        raise upload_error
                finally:
                    if (
                        work_dir.exists()
                        and work_dir.is_dir()
                        and work_dir.parent == self.cache_dir
                    ):
                        await asyncio.to_thread(shutil.rmtree, work_dir)

            project["files"] = files
            project["status"] = "ready"
            project["updated_at"] = utc_now()
            await self.store.put_project(project)
            return project

    async def _download_all(
        self, sources: list[Any], work_dir: Path
    ) -> list[DownloadedAttachment]:
        semaphore = asyncio.Semaphore(self.config.youmind_upload_concurrency)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=30.0)
        ) as client:

            async def download(source: Any) -> DownloadedAttachment:
                async with semaphore:
                    return await download_attachment(
                        client,
                        source,
                        work_dir,
                        max_bytes=self.config.youmind_max_file_bytes,
                    )

            downloaded = await asyncio.gather(*(download(source) for source in sources))
        if sum(item.size for item in downloaded) > self.config.youmind_max_total_bytes:
            raise ValueError("合并转发附件总大小超过限制")
        return downloaded

    def _agent_prompt(self, instruction: str, local_id: str) -> str:
        return (
            f"{instruction.strip()}\n\n"
            "执行偏好：如果当前接口允许选择文字模型，使用 "
            f"{self.config.youmind_text_model}；生成图片时使用 {self.config.youmind_image_model}。"
            "项目中已附上 QQ 合并转发记录和素材，请结合它们完成任务。\n\n"
            f"<!-- qq-task:{local_id} -->"
        )

    async def _recover_chat_id(
        self, client: YouMindClient, board_id: str, local_id: str
    ) -> str:
        chats = await client.call(
            "listChats", {"boardId": board_id, "page": 0, "pageSize": 20}
        )
        if isinstance(chats, dict):
            chats = chats.get("data") or chats.get("items") or chats.get("chats")
        if not isinstance(chats, list):
            return ""
        marker = f"qq-task:{local_id}"
        for candidate in chats:
            if not isinstance(candidate, dict) or not candidate.get("id"):
                continue
            messages = await client.call("listMessages", {"chatId": candidate["id"]})
            values = messages.get("messages") if isinstance(messages, dict) else []
            if any(
                isinstance(item, dict) and marker in str(item.get("content") or "")
                for item in values
                if isinstance(values, list)
            ):
                return str(candidate["id"])
        return ""

    async def _wait_for_turn(
        self,
        client: YouMindClient,
        chat_id: str,
        previous_assistant_id: str,
    ) -> TurnResult:
        deadline = time.monotonic() + self.config.youmind_poll_timeout_seconds
        while True:
            messages = await client.call("listMessages", {"chatId": chat_id})
            turn = parse_turn(messages, previous_assistant_id)
            if turn.kind != "pending":
                return turn
            if time.monotonic() >= deadline:
                raise TimeoutError("YouMind 等待结果超时；任务可能仍在服务端运行")
            await asyncio.sleep(self.config.youmind_poll_interval_seconds)

    async def _deliver_turn(
        self,
        bot: Bot,
        chat: dict[str, Any],
        turn: TurnResult,
        *,
        reply_to: int | None = None,
    ) -> None:
        target = reply_to or int(chat["request_message_id"])
        chat["last_assistant_id"] = turn.assistant_message_id
        chat["updated_at"] = utc_now()
        if turn.kind == "waiting_user":
            chat["status"] = "waiting_user"
            chat["pending_questions"] = turn.questions
            text = "\n\n".join(turn.questions)
            if turn.options:
                text += "\n\n可选项：\n" + "\n".join(
                    f"- {item}" for item in turn.options
                )
            await self.store.put_chat(chat)
            await self._send_text(bot, chat, target, text)
            return
        if turn.kind == "failed":
            raise RuntimeError(turn.error or "YouMind 返回失败")

        media: list[tuple[str, str, str]] = [
            ("image", url, "") for url in turn.image_urls
        ]
        async with self.client() as client:
            for media_id in turn.media_ids:
                payload = await client.call("download", {"id": media_id})
                file_data = payload.get("file") if isinstance(payload, dict) else None
                if isinstance(file_data, dict) and file_data.get("isHidden") is True:
                    await client.call("saveFileToBoard", {"fileId": media_id})
                resolved = media_from_download(payload)
                if not (resolved[0] == "image" and turn.image_urls):
                    media.append(resolved)
        media = [item for item in media if item[1]]
        message = Message(MessageSegment.reply(target))
        for kind, url, _title in media:
            if kind == "video":
                message += MessageSegment.video(url)
            elif kind == "image":
                message += MessageSegment.image(url)
            else:
                message += MessageSegment.text(f"\n{url}")
        text = turn.text.strip()
        if text:
            message += MessageSegment.text(f"\n{text}" if media else text)
        elif not media:
            message += MessageSegment.text("YouMind 已完成，但没有返回可投递的内容。")
        result = await bot.call_api(
            "send_group_msg", group_id=chat["group_id"], message=message
        )
        message_id = send_message_id(result)
        chat["status"] = "completed"
        chat["pending_questions"] = []
        await self.store.put_chat(chat)
        if message_id is not None:
            await self.store.bind_route(message_id, chat["local_id"])

    async def _send_text(
        self,
        bot: Bot,
        chat: dict[str, Any],
        reply_to: int,
        text: str,
    ) -> int | None:
        message = MessageSegment.reply(reply_to) + MessageSegment.text(text)
        result = await bot.call_api(
            "send_group_msg", group_id=chat["group_id"], message=message
        )
        message_id = send_message_id(result)
        if message_id is not None:
            await self.store.bind_route(message_id, chat["local_id"])
        return message_id

    async def _fail(
        self,
        bot: Bot,
        chat: dict[str, Any],
        error: Exception,
        *,
        reply_to: int | None = None,
    ) -> None:
        logger.warning("YouMind task %s failed: %r", chat.get("local_id"), error)
        chat["status"] = "failed"
        chat["error"] = str(error)[:1000]
        chat["updated_at"] = utc_now()
        await self.store.put_chat(chat)
        text = "YouMind 请求失败，请稍后重试。"
        if isinstance(error, ValueError | TimeoutError):
            text = str(error)
        elif isinstance(error, YouMindAPIError) and error.status_code in {402, 429}:
            text = f"YouMind 拒绝了请求：{error.detail}"
        try:
            await self._send_text(
                bot,
                chat,
                reply_to or int(chat["request_message_id"]),
                text,
            )
        except Exception as send_error:  # noqa: BLE001 - delivery failure is logged separately
            logger.warning("Failed to deliver YouMind error: %r", send_error)
