#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import secrets
import stat
import sys
from pathlib import PurePosixPath

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_READ_BYTES = 50_000


def parent_fd(relative: str) -> tuple[int, str]:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must be a workspace-relative file")
    current = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        return current, path.parts[-1]
    except Exception:
        os.close(current)
        raise


def open_regular(parent: int, name: str) -> int:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("path is not a regular file")
    return descriptor


def read_all(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = os.read(descriptor, min(64 * 1024, limit + 1 - size))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            raise ValueError("file is too large to edit")


def read_file(relative: str) -> None:
    parent, name = parent_fd(relative)
    try:
        descriptor = open_regular(parent, name)
        try:
            remaining = MAX_READ_BYTES
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                remaining -= len(chunk)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def edit_file(relative: str, payload_path: str) -> None:
    payload = json.loads(open(payload_path, encoding="utf-8").read())
    old = str(payload["oldText"]).encode()
    new = str(payload["newText"]).encode()
    parent, name = parent_fd(relative)
    temporary = f".ttd-edit-{os.getpid()}-{secrets.token_hex(8)}"
    try:
        source = open_regular(parent, name)
        try:
            source_stat = os.fstat(source)
            current = read_all(source, MAX_FILE_BYTES)
        finally:
            os.close(source)
        if current.count(old) != 1:
            raise ValueError("oldText must match exactly once")
        updated = current.replace(old, new, 1)
        if len(updated) > MAX_FILE_BYTES:
            raise ValueError("edited file would be too large")
        destination = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            stat.S_IMODE(source_stat.st_mode),
            dir_fd=parent,
        )
        try:
            view = memoryview(updated)
            while view:
                view = view[os.write(destination, view) :]
            os.fsync(destination)
        finally:
            os.close(destination)
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def write_file(relative: str, payload_path: str) -> None:
    payload = json.loads(open(payload_path, encoding="utf-8").read())
    content = str(payload["content"]).encode()
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("file content is too large")
    parent, name = parent_fd(relative)
    temporary = f".ttd-write-{os.getpid()}-{secrets.token_hex(8)}"
    try:
        destination = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=parent,
        )
        try:
            view = memoryview(content)
            while view:
                view = view[os.write(destination, view) :]
            os.fsync(destination)
        finally:
            os.close(destination)
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def main() -> None:
    if len(sys.argv) not in {3, 4}:
        raise ValueError("usage: workspace_file.py read PATH | edit PATH PAYLOAD")
    if sys.argv[1] == "read" and len(sys.argv) == 3:
        read_file(sys.argv[2])
        return
    if sys.argv[1] == "edit" and len(sys.argv) == 4:
        edit_file(sys.argv[2], sys.argv[3])
        return
    if sys.argv[1] == "write" and len(sys.argv) == 4:
        write_file(sys.argv[2], sys.argv[3])
        return
    raise ValueError("invalid workspace file operation")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"workspace file operation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
