from __future__ import annotations

from pydantic import BaseModel, Field


class Config(BaseModel):
    auto_ping_proposal_approval_threshold: int = Field(default=3, ge=1)
