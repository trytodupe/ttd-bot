from __future__ import annotations

import ast
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Config(BaseModel):
    mc_server_checker_admins: list[int] = Field(default_factory=list)
    mc_server_checker_interval_seconds: int = 300
    mc_server_checker_player_poll_interval_seconds: int = 10
    mc_server_checker_timeout_seconds: int = 5

    @field_validator("mc_server_checker_admins", mode="before")
    @classmethod
    def _parse_admins(cls, value: Any) -> list[int]:
        if value is None:
            return []
        if isinstance(value, list):
            return [int(v) for v in value if str(v).strip()]
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            parsed: Any = None
            try:
                parsed = json.loads(raw)
            except Exception:
                try:
                    parsed = ast.literal_eval(raw)
                except Exception:
                    parsed = None
            if isinstance(parsed, list):
                return [int(v) for v in parsed if str(v).strip()]
            tokens = [t for t in raw.replace(",", " ").split() if t]
            return [int(t) for t in tokens]
        return [int(value)]
