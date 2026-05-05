from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccessRequestConfig:
    capability_name: str = "moellmchats.private_chat"
    prompt_text: str = "发送“申请”来申请开启私聊功能。"

