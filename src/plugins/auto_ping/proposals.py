from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Any

import nonebot_plugin_localstore as store

from .storage import normalize_alias


PROPOSAL_TTL_SECONDS = 12 * 60 * 60


@dataclass(frozen=True, slots=True)
class AliasProposal:
    message_id: int
    group_id: int
    proposer_qq: int
    target_qq: int
    alias: str
    approve_emoji_id: str
    reject_emoji_id: str
    created_at: int


@dataclass(frozen=True, slots=True)
class ReactionNotice:
    message_id: int
    group_id: int
    user_id: int
    emoji_id: str
    count: int
    is_add: bool


def get_proposals_file() -> Path:
    return store.get_data_file(plugin_name="auto_ping", filename="proposals.json")


def create_proposal(
    *,
    message_id: int,
    group_id: int,
    proposer_qq: int,
    target_qq: int,
    alias: str,
    approve_emoji_id: str,
    reject_emoji_id: str,
    created_at: int | None = None,
) -> AliasProposal:
    return AliasProposal(
        message_id=int(message_id),
        group_id=int(group_id),
        proposer_qq=int(proposer_qq),
        target_qq=int(target_qq),
        alias=normalize_alias(alias),
        approve_emoji_id=str(approve_emoji_id),
        reject_emoji_id=str(reject_emoji_id),
        created_at=int(created_at or time.time()),
    )


class ProposalStore:
    def __init__(self, file_path: Path | None = None):
        self.file_path = file_path or get_proposals_file()
        self._proposals: dict[tuple[int, int], AliasProposal] = {}
        self.load()

    def load(self) -> None:
        proposals: dict[tuple[int, int], AliasProposal] = {}
        if self.file_path.exists():
            try:
                with self.file_path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception:
                payload = {}

            items = payload.get("proposals", []) if isinstance(payload, dict) else []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    try:
                        proposal = create_proposal(**item)
                    except (TypeError, ValueError):
                        continue
                    proposals[(proposal.group_id, proposal.message_id)] = proposal

        self._proposals = proposals

    def _save(self) -> None:
        payload = {
            "proposals": [
                asdict(proposal)
                for proposal in sorted(
                    self._proposals.values(),
                    key=lambda item: (item.created_at, item.group_id, item.message_id),
                )
            ]
        }
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)

    def all(self) -> list[AliasProposal]:
        return list(self._proposals.values())

    def get(self, group_id: int, message_id: int) -> AliasProposal | None:
        return self._proposals.get((int(group_id), int(message_id)))

    def find_by_alias(self, alias: str) -> AliasProposal | None:
        normalized = normalize_alias(alias)
        return next(
            (proposal for proposal in self._proposals.values() if proposal.alias == normalized),
            None,
        )

    def add(self, proposal: AliasProposal) -> None:
        self._proposals[(proposal.group_id, proposal.message_id)] = proposal
        self._save()

    def remove(self, group_id: int, message_id: int) -> AliasProposal | None:
        proposal = self._proposals.pop((int(group_id), int(message_id)), None)
        if proposal is not None:
            self._save()
        return proposal

    def expire(self, *, now: int | None = None) -> list[AliasProposal]:
        current_time = int(time.time() if now is None else now)
        expired = [
            proposal
            for proposal in self._proposals.values()
            if proposal.created_at + PROPOSAL_TTL_SECONDS <= current_time
        ]
        for proposal in expired:
            self._proposals.pop((proposal.group_id, proposal.message_id), None)
        if expired:
            self._save()
        return expired


def parse_reaction_notice(event: Any) -> ReactionNotice | None:
    if getattr(event, "notice_type", None) != "group_msg_emoji_like":
        return None

    try:
        message_id = int(event.message_id)
        group_id = int(event.group_id)
        user_id = int(event.user_id)
    except (AttributeError, TypeError, ValueError):
        return None

    likes = getattr(event, "likes", None)
    if not isinstance(likes, list) or not likes:
        return None
    like = likes[0]
    if not isinstance(like, dict):
        return None

    emoji_id = str(like.get("emoji_id", "")).strip()
    if not emoji_id:
        return None
    try:
        count = int(like.get("count", 0))
    except (TypeError, ValueError):
        return None

    return ReactionNotice(
        message_id=message_id,
        group_id=group_id,
        user_id=user_id,
        emoji_id=emoji_id,
        count=max(0, count),
        is_add=getattr(event, "sub_type", None) == "add",
    )
