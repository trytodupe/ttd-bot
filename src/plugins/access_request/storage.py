from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

import nonebot_plugin_localstore as store


RequestStatus = Literal["pending", "approved", "rejected"]


@dataclass(slots=True)
class AccessRequestRecord:
    request_id: str
    capability: str
    user_id: int
    request_text: str
    status: RequestStatus
    created_at: int
    reviewed_by: int | None = None
    reviewed_at: int | None = None


DATA_FILE: Path = store.get_data_file(plugin_name="access_request", filename="requests.json")


def _load_payload() -> dict[str, object]:
    if not DATA_FILE.exists():
        return {"requests": []}
    try:
        with DATA_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {"requests": []}
    if not isinstance(payload, dict):
        return {"requests": []}
    if not isinstance(payload.get("requests"), list):
        payload["requests"] = []
    return payload


def _save_payload(payload: dict[str, object]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)


def load_requests() -> list[AccessRequestRecord]:
    payload = _load_payload()
    requests: list[AccessRequestRecord] = []
    for item in payload.get("requests", []):
        if not isinstance(item, dict):
            continue
        try:
            requests.append(AccessRequestRecord(**item))
        except TypeError:
            continue
    return requests


def save_requests(requests: list[AccessRequestRecord]) -> None:
    _save_payload({"requests": [asdict(request) for request in requests]})


def create_request(capability: str, user_id: int, request_text: str, *, created_at: int | None = None) -> AccessRequestRecord:
    return AccessRequestRecord(
        request_id=uuid4().hex,
        capability=capability,
        user_id=int(user_id),
        request_text=request_text,
        status="pending",
        created_at=int(created_at or time.time()),
    )
