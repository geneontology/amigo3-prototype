"""Read-only file inspection rooted in an agent's workspace directory descriptor."""

from __future__ import annotations

import contextlib
import os
import stat
from datetime import UTC, datetime
from pathlib import PurePosixPath

from fastapi import HTTPException

MAX_PREVIEW = 200_000
MAX_DOWNLOAD = 10_000_000
MAX_ENTRIES = 500


@contextlib.contextmanager
def workspace_file(root_fd: int, path: str, directory=False):
    """Open every path component relative to the retained root; never follow symlinks."""
    if len(path) > 4096 or "\x00" in path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise HTTPException(400, "Use a relative path within this session workspace")
    parts = PurePosixPath(path).parts
    fd = None
    try:
        fd = os.dup(root_fd)
        for i, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(parts) - 1 or directory:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        mode = os.fstat(fd).st_mode
        if (directory and not stat.S_ISDIR(mode)) or (not directory and not stat.S_ISREG(mode)):
            raise HTTPException(400, "Only regular files and directories can be viewed")
        yield fd
    except OSError as exc:
        raise HTTPException(
            404, "File unavailable; symbolic links and special files cannot be opened"
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)


def list_files(root_fd: int, path: str):
    with workspace_file(root_fd, path, directory=True) as fd:
        entries = []
        truncated = False
        with os.scandir(fd) as scan:
            for entry in scan:
                if len(entries) == MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                kind = (
                    "directory"
                    if stat.S_ISDIR(info.st_mode)
                    else "file"
                    if stat.S_ISREG(info.st_mode)
                    else "unavailable"
                )
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(PurePosixPath(path) / entry.name),
                        "kind": kind,
                        "size": info.st_size,
                        "modified_at": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
                    }
                )
        return {
            "path": str(PurePosixPath(path)),
            "entries": sorted(entries, key=lambda e: (e["kind"] != "directory", e["name"].lower())),
            "truncated": truncated,
        }


def read_file(root_fd: int, path: str, download=False):
    with workspace_file(root_fd, path) as fd:
        info = os.fstat(fd)
        limit = MAX_DOWNLOAD if download else MAX_PREVIEW
        if download and info.st_size > limit:
            raise HTTPException(413, "Download exceeds the 10 MB limit")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(limit + 1)
        if download:
            if len(data) > limit:
                raise HTTPException(413, "Download exceeds the 10 MB limit")
            return data
        truncated = len(data) > limit
        data = data[:limit]
        try:
            # A preview may end inside a UTF-8 character; only that final partial character is discarded.
            import codecs

            text = codecs.getincrementaldecoder("utf-8")().decode(data, final=not truncated)
            binary = "\x00" in text
        except UnicodeDecodeError:
            text, binary = "", True
        return {
            "path": path,
            "size": info.st_size,
            "text": None if binary else text,
            "binary": binary,
            "truncated": truncated,
        }
