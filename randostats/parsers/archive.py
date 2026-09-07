"""Helpers for exports that arrive as a zip rather than a single file."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Iterator

ZIP_MAGIC = b"PK\x03\x04"

# An export is other people's data; a crafted one should not be able to
# exhaust memory. These caps are far above any real Telegram or Meta export.
MAX_MEMBERS = 50_000
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024


class ArchiveTooLarge(ValueError):
    """The archive claims to hold more than we are willing to unpack."""


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
    """Yield (path, parsed json) for archive members matching the filters, in path order.

    Refuses archives that declare more members or more uncompressed bytes than
    the caps above, so a zip bomb fails fast instead of filling memory.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ArchiveTooLarge(f"archive holds {len(infos)} files, more than the {MAX_MEMBERS} allowed")
        declared = sum(i.file_size for i in infos)
        if declared > MAX_TOTAL_BYTES:
            raise ArchiveTooLarge(f"archive unpacks to {declared // (1024 * 1024)} MB, more than the "
                                  f"{MAX_TOTAL_BYTES // (1024 * 1024)} MB allowed")
        for info in sorted(infos, key=lambda i: i.filename):
            name = info.filename
            if not name.endswith(suffix) or (contains and contains not in name):
                continue
            if info.file_size > MAX_MEMBER_BYTES:
                raise ArchiveTooLarge(f"{name} unpacks to {info.file_size // (1024 * 1024)} MB, which is too large")
            try:
                yield name, json.loads(zf.read(name).decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, KeyError):
                continue
