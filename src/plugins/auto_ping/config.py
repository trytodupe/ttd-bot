from __future__ import annotations

import ast
import json

from pydantic import BaseModel, Field
from pydantic import field_validator


class AutoPingTarget(BaseModel):
    qq: int
    aliases: list[str] = Field(default_factory=list)


class Config(BaseModel):
    """
    auto_ping_alias_map:
      A simple alias -> QQ mapping.
      Example (in .env): auto_ping_alias_map='{\"bob\":123,\"alice\":456}'

    auto_ping_targets:
      A list of targets, each with multiple aliases pointing to one QQ.
      Example (in .env): auto_ping_targets='[{\"qq\":123,\"aliases\":[\"bob\",\"b\"]}]'
    """

    auto_ping_alias_map: dict[str, int] = Field(default_factory=dict)
    auto_ping_targets: list[AutoPingTarget] = Field(default_factory=list)

    @field_validator("auto_ping_alias_map", mode="before")
    @classmethod
    def _parse_alias_map(cls, value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = ast.literal_eval(raw)
            if not isinstance(parsed, dict):
                raise TypeError("auto_ping_alias_map must be a dict")
            return parsed
        return value

    @field_validator("auto_ping_targets", mode="before")
    @classmethod
    def _parse_targets(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = ast.literal_eval(raw)
            if not isinstance(parsed, list):
                raise TypeError("auto_ping_targets must be a list")
            return parsed
        return value
