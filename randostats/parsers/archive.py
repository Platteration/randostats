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

# A zip bomb is not a big archive, it is a big *expansion*: repetitive JSON
# deflates a few hundred times over, so half a gigabyte of declared content
# used to be reachable from a two-megabyte upload, and parsing that costs
# several times its size again in Python objects. Real message exports are
# ordinary prose and deflate five to fifteen times, so refuse anything
# claiming fifty times its own size - above a floor, since a small archive
# of very repetitive JSON is not suspicious on its own.
MAX_EXPANSION = 50
EXPANSION_FLOOR = 32 * 1024 * 1024

_EOCD = b"PK\x05\x06"
_EOCD64 = b"PK\x06\x06"


class ArchiveTooLarge(ValueError):
    """The archive claims to hold more than we are willing to unpack."""


def is_zip(data: bytes) -> bool:
    return data[:4] == ZIP_MAGIC


def declared_members(data: bytes) -> int | None:
    """How many members the archive's own index claims, or None if it cannot say.

    ``zipfile.ZipFile`` reads a header for every entry as it opens, so by the
    time there is an ``infolist`` to count, millions of members have already
    cost seconds and gigabytes. The end-of-central-directory record carries
    the count in two bytes at a known offset from the end of the file, and a
    Zip64 archive parks 0xFFFF there and repeats it in a record of its own.
    """
    start = data.rfind(_EOCD, max(0, len(data) - (22 + 0xFFFF)))
    if start < 0 or start + 22 > len(data):
        return None
    count = int.from_bytes(data[start + 10:start + 12], "little")
    if count != 0xFFFF:
        return count
    start = data.rfind(_EOCD64, 0, start)
    if start < 0 or start + 40 > len(data):
        return None
    return int.from_bytes(data[start + 32:start + 40], "little")


def _refuse_too_many_members(data: bytes) -> None:
    """Raise before opening an archive that says it holds more than we allow."""
    declared = declared_members(data)
    if declared is not None and declared > MAX_MEMBERS:
        raise ArchiveTooLarge(f"archive holds {declared} files, more than the {MAX_MEMBERS} allowed")


def names(data: bytes) -> list[str]:
    """Every file path inside the archive, or an empty list if it isn't one.

    Format detection calls this on the raw upload, so the member cap has to
    apply here too: it used to be checked only once the archive was being
    read for messages, by which point the index was already in memory.
    """
    if not is_zip(data):
        return []
    _refuse_too_many_members(data)
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
    _refuse_too_many_members(data)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ArchiveTooLarge(f"archive holds {len(infos)} files, more than the {MAX_MEMBERS} allowed")
        declared = sum(i.file_size for i in infos)
        allowed = min(MAX_TOTAL_BYTES, max(EXPANSION_FLOOR, len(data) * MAX_EXPANSION))
        if declared > allowed:
            raise ArchiveTooLarge(f"archive unpacks to {declared // (1024 * 1024)} MB, more than the "
                                  f"{allowed // (1024 * 1024)} MB allowed for an archive this size")
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
