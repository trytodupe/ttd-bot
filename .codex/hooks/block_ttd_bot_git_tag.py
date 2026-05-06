#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path


TARGET_REPO = Path("/Users/ttd/repositories/qqBot/ttd-bot").resolve()
DEPLOY_TAG_SCRIPT = str(TARGET_REPO / "scripts" / "create_deploy_tag.py")
BLOCK_REASON = (
    f"Do not use raw git tag for ttd-bot. Use {DEPLOY_TAG_SCRIPT} instead."
)
SHELL_SEPARATOR_RE = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


def _resolve_path(path_text: str, base_dir: Path) -> Path:
    path = Path(os.path.expanduser(path_text))
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def _is_target_repo(path: Path) -> bool:
    try:
        return path == TARGET_REPO or TARGET_REPO in path.parents
    except RuntimeError:
        return False


def _segment_tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return []


def _git_invokes_tag(tokens: list[str]) -> tuple[bool, int | None]:
    if not tokens or tokens[0] != "git":
        return False, None

    repo_index: int | None = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-C" and index + 1 < len(tokens):
            repo_index = index + 1
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token == "tag", repo_index
    return False, repo_index


def _command_targets_repo(command: str, initial_cwd: Path) -> bool:
    current_dir = initial_cwd

    for segment in SHELL_SEPARATOR_RE.split(command):
        stripped = segment.strip()
        if not stripped:
            continue

        tokens = _segment_tokens(stripped)
        if not tokens:
            continue

        if tokens[0] == "cd" and len(tokens) >= 2:
            current_dir = _resolve_path(tokens[1], current_dir)
            continue

        is_tag, repo_index = _git_invokes_tag(tokens)
        if not is_tag:
            continue

        repo_dir = current_dir
        if repo_index is not None:
            repo_dir = _resolve_path(tokens[repo_index], current_dir)

        if _is_target_repo(repo_dir):
            return True

    return False


def main() -> int:
    payload = json.load(sys.stdin)
    cwd = Path(payload.get("cwd") or ".").resolve()
    command = payload.get("tool_input", {}).get("command")

    if not isinstance(command, str):
        return 0

    if _command_targets_repo(command, cwd):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": BLOCK_REASON,
                    }
                }
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
