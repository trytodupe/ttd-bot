from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


def project_key(group_id: int, forward_message_id: int) -> str:
    return f"{group_id}:{forward_message_id}"


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self._state = self._load()

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "projects": {}, "chats": {}, "routes": {}}

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        for key in ("projects", "chats", "routes"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        data["version"] = 1
        return data

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temp_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(temp_path, 0o600)
        temp_path.replace(self.path)

    async def get_project(
        self, group_id: int, forward_message_id: int
    ) -> dict[str, Any] | None:
        async with self._lock:
            value = self._state["projects"].get(
                project_key(group_id, forward_message_id)
            )
            return deepcopy(value) if isinstance(value, dict) else None

    async def put_project(self, project: dict[str, Any]) -> None:
        key = project_key(int(project["group_id"]), int(project["forward_message_id"]))
        async with self._lock:
            self._state["projects"][key] = deepcopy(project)
            self._write()

    async def put_chat(self, chat: dict[str, Any]) -> None:
        async with self._lock:
            self._state["chats"][str(chat["local_id"])] = deepcopy(chat)
            self._write()

    async def get_chat(self, local_id: str) -> dict[str, Any] | None:
        async with self._lock:
            value = self._state["chats"].get(str(local_id))
            return deepcopy(value) if isinstance(value, dict) else None

    async def route(self, message_id: int) -> dict[str, Any] | None:
        async with self._lock:
            local_id = self._state["routes"].get(str(message_id))
            value = self._state["chats"].get(str(local_id)) if local_id else None
            return deepcopy(value) if isinstance(value, dict) else None

    async def bind_route(self, message_id: int, local_id: str) -> None:
        async with self._lock:
            self._state["routes"][str(message_id)] = str(local_id)
            self._write()

    async def unfinished_chats(self) -> list[dict[str, Any]]:
        terminal = {"completed", "failed", "cancelled", "waiting_user"}
        async with self._lock:
            return [
                deepcopy(value)
                for value in self._state["chats"].values()
                if isinstance(value, dict) and value.get("status") not in terminal
            ]

    async def chats_for(self, group_id: int, user_id: int) -> list[dict[str, Any]]:
        async with self._lock:
            values = [
                deepcopy(value)
                for value in self._state["chats"].values()
                if isinstance(value, dict)
                and int(value.get("group_id", 0)) == group_id
                and int(value.get("user_id", 0)) == user_id
            ]
        return sorted(
            values, key=lambda item: str(item.get("updated_at", "")), reverse=True
        )
