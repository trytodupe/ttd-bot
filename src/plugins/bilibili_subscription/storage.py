from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import nonebot_plugin_localstore as store
from nonebot import logger


def legacy_state_path() -> Path:
    return store.get_data_file(
        plugin_name="nonebot_plugin_parser",
        filename="bilibili_subscriptions.json",
    )


class SubscriptionManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or legacy_state_path()
        self._subs: dict[tuple[str, str], set[str]] = {}
        self._last_seen: dict[str, str] = {}
        self._last_live_start: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._save()
            return

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            logger.warning(f"订阅文件损坏，按空状态加载: {self.path}")
            return
        if not isinstance(data, dict):
            return

        subscriptions = data.get("subscriptions", [])
        if isinstance(subscriptions, list):
            for entry in subscriptions:
                if not isinstance(entry, dict):
                    continue
                scope = entry.get("scope")
                group_id = entry.get("group_id")
                uid = entry.get("uid")
                if (
                    isinstance(scope, str)
                    and scope
                    and isinstance(group_id, str)
                    and group_id
                    and isinstance(uid, str)
                    and uid
                ):
                    self._subs.setdefault((scope, group_id), set()).add(uid)

        last_seen = data.get("last_seen", {})
        if isinstance(last_seen, dict):
            for raw_uid, raw_info in last_seen.items():
                if not isinstance(raw_uid, str) or not isinstance(raw_info, dict):
                    continue
                dynamic_id = raw_info.get("last_dynamic_id", "0")
                self._last_seen[raw_uid] = str(dynamic_id)
                live_start = raw_info.get("last_live_start")
                if live_start is not None:
                    try:
                        self._last_live_start[raw_uid] = int(live_start)
                    except (TypeError, ValueError):
                        pass

        logger.info(
            f"已加载 {sum(map(len, self._subs.values()))} 条 B 站订阅，"
            f"{len(self._last_seen)} 个 UID 的检查记录"
        )

    def _save(self) -> None:
        subscriptions = [
            {"scope": scope, "group_id": group_id, "uid": uid}
            for (scope, group_id), uids in sorted(self._subs.items())
            for uid in sorted(uids)
        ]
        tracked_uids = self._last_seen.keys() | self._last_live_start.keys()
        last_seen: dict[str, dict[str, Any]] = {}
        for uid in sorted(tracked_uids):
            info: dict[str, Any] = {
                "last_dynamic_id": self._last_seen.get(uid, "0"),
                "last_checked": time.time(),
            }
            if uid in self._last_live_start:
                info["last_live_start"] = self._last_live_start[uid]
            last_seen[uid] = info

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"subscriptions": subscriptions, "last_seen": last_seen},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def add_sub(self, scope: str, group_id: str, uid: str) -> bool:
        uids = self._subs.setdefault((scope, group_id), set())
        if uid in uids:
            return False
        uids.add(uid)
        self._save()
        return True

    def remove_sub(self, scope: str, group_id: str, uid: str) -> bool:
        key = (scope, group_id)
        uids = self._subs.get(key)
        if not uids or uid not in uids:
            return False
        uids.remove(uid)
        if not uids:
            del self._subs[key]
        self._save()
        return True

    def get_subs_for_group(self, scope: str, group_id: str) -> list[str]:
        return sorted(self._subs.get((scope, group_id), set()))

    def get_groups_for_uid(self, uid: str) -> list[tuple[str, str]]:
        return sorted(key for key, uids in self._subs.items() if uid in uids)

    def get_all_uids(self) -> list[str]:
        return sorted({uid for uids in self._subs.values() for uid in uids})

    def get_last_seen(self, uid: str) -> str:
        return self._last_seen.get(uid, "0")

    def set_last_seen(self, uid: str, dynamic_id: str) -> None:
        self._last_seen[uid] = dynamic_id
        self._save()

    def get_last_live_start(self, uid: str) -> int | None:
        return self._last_live_start.get(uid)

    def set_last_live_start(self, uid: str, live_start: int) -> None:
        self._last_live_start[uid] = live_start
        self._save()
