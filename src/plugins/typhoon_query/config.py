from __future__ import annotations

from pydantic import BaseModel


class Config(BaseModel):
    typhoon_api_timeout: int = 10
    typhoon_cache_seconds: int = 600
