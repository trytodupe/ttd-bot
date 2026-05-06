#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys


MAX_TAG_MESSAGE_LENGTH = 50


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip()


def _fit(text: str, max_length: int = MAX_TAG_MESSAGE_LENGTH) -> str:
    normalized = _normalize(text)
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length].rstrip(" .,;:，。；：-_+/")


def _run_git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an annotated deploy tag")
    parser.add_argument("tag_name")
    parser.add_argument("commit_sha")
    parser.add_argument("message")
    args = parser.parse_args()

    message = _fit(args.message)
    if not message:
        print("empty tag message", file=sys.stderr)
        return 1

    try:
        _run_git(["tag", "-a", args.tag_name, args.commit_sha, "-m", message])
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        return exc.returncode

    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
