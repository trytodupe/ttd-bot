from __future__ import annotations

from pydantic import BaseModel, Field


class Config(BaseModel):
    dev_agent_enabled: bool = False
    dev_agent_socket_path: str = "/run/ttd-dev-agent/controller.sock"
    dev_agent_socket_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    dev_agent_outbox_poll_seconds: float = Field(default=1.0, ge=0.1, le=30)
