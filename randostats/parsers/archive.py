"""Helpers for exports that arrive as a zip rather than a single file."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Iterator

ZIP_MAGIC = b"PK\x03\x04"


def is_zip(data: bytes) -> bool:
    return data[:4] == ZIP_MAGIC


def names(data: bytes) -> list[str]:
    """Every file path inside the archive, or an empty list if it isn't one."""
    if not is_zip(data):
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return zf.namelist()
    except zipfile.BadZipFile:
        return []


def json_files(data: bytes, suffix: str = ".json", contains: str = "") -> Iterator[tuple[str, object]]:
    """Yield (path, parsed json) for archive members matching the filters, in path order."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in sorted(zf.namelist()):
            if not name.endswith(suffix) or (contains and contains not in name):
                continue
            try:
                yield name, json.loads(zf.read(name).decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, KeyError):
                continue
