from __future__ import annotations

import ast
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Config(BaseModel):
    easy_trigger_user_blacklist: dict[str, set[str]] = Field(default_factory=dict)
    easy_trigger_user_whitelist: dict[str, set[str]] = Field(default_factory=dict)
    easy_trigger_group_blacklist: dict[str, set[str]] = Field(default_factory=dict)
    easy_trigger_group_whitelist: dict[str, set[str]] = Field(default_factory=dict)

    @field_validator(
        "easy_trigger_user_blacklist",
        "easy_trigger_user_whitelist",
        "easy_trigger_group_blacklist",
        "easy_trigger_group_whitelist",
        mode="before",
    )
    @classmethod
    def _parse_trigger_id_map(cls, value: Any) -> dict[str, set[str]]:
        if value is None or value == "":
            return {}
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return {}
            try:
                value = json.loads(raw)
            except Exception:
                try:
                    value = ast.literal_eval(raw)
                except Exception as exc:
                    raise ValueError("Expected a JSON or Python dict literal") from exc
        if not isinstance(value, dict):
            raise ValueError("Expected a dict keyed by trigger name")

        parsed: dict[str, set[str]] = {}
        for trigger_name, ids in value.items():
            key = str(trigger_name).strip()
            if not key:
                continue
            parsed[key] = _normalize_ids(ids)
        return parsed


def _normalize_ids(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return set()
        parsed: Any = None
        try:
            parsed = json.loads(raw)
        except Exception:
            try:
                parsed = ast.literal_eval(raw)
            except Exception:
                parsed = None
        if isinstance(parsed, list | tuple | set):
            return {str(item).strip() for item in parsed if str(item).strip()}
        return {token for token in raw.replace(",", " ").split() if token}
    if isinstance(value, list | tuple | set):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()} if str(value).strip() else set()
