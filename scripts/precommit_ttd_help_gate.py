#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def _head_ref() -> str:
    result = _run_git("rev-parse", "--verify", "HEAD", check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    return EMPTY_TREE


def _tracked_plugins(ref: str) -> set[str]:
    result = _run_git("ls-tree", "-r", "--name-only", ref, "src/plugins", check=False)
    if result.returncode != 0:
        return set()
    plugins = set()
    for raw_path in result.stdout.splitlines():
        parts = Path(raw_path).parts
        if len(parts) >= 3 and parts[0] == "src" and parts[1] == "plugins":
            plugins.add(parts[2])
    return plugins


def _staged_paths() -> list[str]:
    result = _run_git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [line for line in result.stdout.splitlines() if line]


def _plugin_name_for_path(path: str) -> str | None:
    parts = Path(path).parts
    if len(parts) >= 3 and parts[0] == "src" and parts[1] == "plugins":
        return parts[2]
    return None


def _has_new_plugin_dir(staged_paths: list[str], tracked_plugins: set[str]) -> bool:
    for path in staged_paths:
        plugin_name = _plugin_name_for_path(path)
        if plugin_name and plugin_name not in tracked_plugins:
            return True
    return False


def _nonebot_plugins_from_text(pyproject_text: str) -> set[str]:
    data = tomllib.loads(pyproject_text)
    tool = data.get("tool", {})
    nonebot = tool.get("nonebot", {})
    plugins = nonebot.get("plugins", [])
    return {plugin for plugin in plugins if isinstance(plugin, str)}


def _read_staged_file(path: str) -> str | None:
    result = _run_git("show", f":{path}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _read_ref_file(ref: str, path: str) -> str | None:
    result = _run_git("show", f"{ref}:{path}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _has_new_nonebot_plugin(staged_pyproject: str, head_pyproject: str | None) -> bool:
    staged_plugins = _nonebot_plugins_from_text(staged_pyproject)
    head_plugins = _nonebot_plugins_from_text(head_pyproject) if head_pyproject else set()
    return bool(staged_plugins - head_plugins)


def should_run() -> bool:
    head_ref = _head_ref()
    tracked_plugins = _tracked_plugins(head_ref)
    staged_paths = _staged_paths()

    if _has_new_plugin_dir(staged_paths, tracked_plugins):
        print("ttd-help-coverage: detected new local plugin directory")
        return True

    if "pyproject.toml" not in staged_paths:
        print("ttd-help-coverage: skip, no relevant staged changes")
        return False

    staged_pyproject = _read_staged_file("pyproject.toml")
    if staged_pyproject is None:
        print("ttd-help-coverage: skip, staged pyproject.toml not found")
        return False

    head_pyproject = _read_ref_file(head_ref, "pyproject.toml")
    if _has_new_nonebot_plugin(staged_pyproject, head_pyproject):
        print("ttd-help-coverage: detected new nonebot plugin in pyproject.toml")
        return True

    print("ttd-help-coverage: skip, no new plugin directory or nonebot plugin")
    return False


def main() -> int:
    if not should_run():
        return 0

    command = ["uv", "run", "pytest", "tests/test_ttd_help.py"]
    return subprocess.run(command, cwd=REPO_ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
