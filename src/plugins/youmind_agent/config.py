from __future__ import annotations

from pydantic import BaseModel, Field


class Config(BaseModel):
    youmind_enabled: bool = False
    youmind_api_key: str = ""
    youmind_base_url: str = "https://youmind.com"
    youmind_proxy: str = ""
    youmind_allowed_group_ids: set[int] = Field(default_factory=lambda: {1015880675})
    youmind_text_model: str = "gpt-5.6-luna"
    youmind_image_model: str = "gpt-image-2-2026-04-21"
    youmind_image_skill_id: str = "019c4d56-f23a-78a7-9ebb-af41b8993a0e"
    youmind_image_skill_name: str = "Create image"
    youmind_max_files: int = Field(default=30, ge=1, le=100)
    youmind_max_file_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    youmind_max_total_bytes: int = Field(default=500 * 1024 * 1024, ge=1)
    youmind_max_forward_depth: int = Field(default=2, ge=0, le=5)
    youmind_upload_concurrency: int = Field(default=3, ge=1, le=10)
    youmind_chat_timeout_seconds: float = Field(default=120.0, ge=10.0)
    youmind_poll_interval_seconds: float = Field(default=5.0, ge=1.0)
    youmind_poll_timeout_seconds: float = Field(default=1800.0, ge=30.0)
