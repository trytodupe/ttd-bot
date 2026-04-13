from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class UserStorage:
    """Persistent user binding storage for TETR.IO accounts."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._users: dict[str, str] = {}
        self._load()

    @staticmethod
    def _normalize_username(value: Any) -> str | None:
        if isinstance(value, dict):
            username = (
                value.get("tetr_user")
                or value.get("username")
                or value.get("user")
                or value.get("name")
            )
        elif isinstance(value, (list, tuple)) and value:
            username = value[0]
        else:
            username = value

        username = str(username).strip()
        if not username:
            return None
        return username

    @classmethod
    def _decode_payload(cls, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            return {}

        raw_users = payload.get("users")
        if not isinstance(raw_users, dict):
            raw_users = payload

        users: dict[str, str] = {}
        for raw_user_id, raw_binding in raw_users.items():
            user_id = str(raw_user_id).strip()
            if not user_id:
                continue
            binding = cls._normalize_username(raw_binding)
            if binding is None:
                continue
            users[user_id] = binding
        return users

    def _load(self) -> None:
        if not self.file_path.exists():
            self._users = {}
            return

        try:
            with self.file_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self._users = {}
            return

        self._users = self._decode_payload(payload)

    def _save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "users": {
                user_id: username
                for user_id, username in sorted(self._users.items())
            }
        }
        with self.file_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def add_user(self, user_id: str, username: str) -> bool:
        normalized_user_id = str(user_id).strip()
        normalized_username = str(username).strip()
        if not normalized_user_id or not normalized_username:
            raise ValueError("Invalid tetr binding")

        if self._users.get(normalized_user_id) == normalized_username:
            return False

        self._users[normalized_user_id] = normalized_username
        self._save()
        return True

    def remove_user(self, user_id: str) -> bool:
        normalized_user_id = str(user_id).strip()
        if normalized_user_id not in self._users:
            return False

        self._users.pop(normalized_user_id, None)
        self._save()
        return True

    def has_user(self, user_id: str) -> bool:
        return str(user_id).strip() in self._users

    def get_all_users(self) -> dict[str, str]:
        return self._users.copy()

    def get_single_user(self, user_id: str) -> str:
        return self._users[str(user_id).strip()]

    def clear_all(self) -> None:
        self._users.clear()
        self._save()
