from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


_WINDOW_24H = 86400.0
_TRIM_AFTER = 86400.0 * 2  # 48h retention
_DEDUP_WINDOW = 30.0  # skip duplicate snapshots within 30s


class HistoryStorage:
    """Persistent snapshot history for TETR.IO stats, used for 24h-ago diffs."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._data: dict[str, list[tuple[float, dict[str, Any]]]] = {}
        self._load()

    def _load(self) -> None:
        if not self.file_path.exists():
            self._data = {}
            return
        try:
            with self.file_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self._data = {}
            return

        if not isinstance(payload, dict):
            self._data = {}
            return

        loaded: dict[str, list[tuple[float, dict[str, Any]]]] = {}
        for key, entries in payload.items():
            if not isinstance(entries, list):
                continue
            cleaned: list[tuple[float, dict[str, Any]]] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("ts")
                data = entry.get("data")
                if not isinstance(ts, (int, float)) or not isinstance(data, dict):
                    continue
                cleaned.append((float(ts), data))
            cleaned.sort(key=lambda item: item[0])
            if cleaned:
                loaded[str(key)] = cleaned
        self._data = loaded

    def _save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: [{"ts": ts, "data": data} for ts, data in entries]
            for key, entries in self._data.items()
        }
        with self.file_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def record(self, key: str, data: dict[str, Any], now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()

        entries = self._data.get(key, [])
        if entries and now - entries[-1][0] < _DEDUP_WINDOW:
            return  # skip near-duplicate snapshot

        entries.append((now, data))

        cutoff = now - _TRIM_AFTER
        if entries and entries[0][0] < cutoff:
            entries = [e for e in entries if e[0] >= cutoff]

        self._data[key] = entries
        self._save()

    def get_closest_to_24h_ago(
        self, key: str, now: Optional[float] = None
    ) -> Optional[dict[str, Any]]:
        if now is None:
            now = time.time()

        entries = self._data.get(key)
        if not entries:
            return None

        target = now - _WINDOW_24H
        best: Optional[tuple[float, dict[str, Any]]] = None
        best_dist = float("inf")
        for ts, data in entries:
            dist = abs(ts - target)
            if dist < best_dist:
                best_dist = dist
                best = (ts, data)
        return best[1] if best else None

    def clear(self) -> None:
        self._data = {}
        try:
            self.file_path.unlink(missing_ok=True)
        except OSError:
            pass