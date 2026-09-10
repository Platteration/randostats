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

# The fixed part of one central directory entry, and so the least an entry
# can cost; a real export's paths carry it well past 150 bytes, so allow a
# generous quarter-kilobyte a member before an index is judged too big.
CD_ENTRY_HEADER = 46
BYTES_PER_MEMBER = 250

_EOCD = b"PK\x05\x06"
_EOCD64 = b"PK\x06\x06"


class ArchiveTooLarge(ValueError):
    """The archive claims to hold more than we are willing to unpack."""


def is_zip(data: bytes) -> bool:
    return data[:4] == ZIP_MAGIC


def _end_records(data: bytes) -> tuple[int, int]:
    """Offsets of the end-of-central-directory record and its Zip64 twin, or -1."""
    start = data.rfind(_EOCD, max(0, len(data) - (22 + 0xFFFF)))
    if start < 0 or start + 22 > len(data):
        return -1, -1
    wide = data.rfind(_EOCD64, 0, start)
    return start, wide if wide >= 0 and wide + 56 <= len(data) else -1


def declared_members(data: bytes) -> int | None:
    """How many members the archive's own index claims, or None if it cannot say.

    This is what the uploader *says*, and nothing checks it: the count lives
    in two bytes of the end-of-central-directory record, and CPython's
    ``zipfile`` never reads them. Two edited bytes make a 300,000-member
    archive claim it holds one and it is still enumerated in full. Keep it
    only as the cheap exact answer for the honest archives that dominate, and
    bound the real cost with ``index_bytes`` below.

    A Zip64 archive parks 0xFFFF here and repeats the count in a record of
    its own; with no such record the count is simply unknown, which is one
    more reason not to lean on it.
    """
    start, wide = _end_records(data)
    if start < 0:
        return None
    count = int.from_bytes(data[start + 10:start + 12], "little")
    if count != 0xFFFF:
        return count
    if wide < 0:
        return None
    return int.from_bytes(data[wide + 32:wide + 40], "little")


def index_bytes(data: bytes) -> int | None:
    """How much central directory ``zipfile`` will walk to open this, or None.

    This is the number that cannot be lied down. ``_RealGetContents`` seeks to
    the central directory, reads ``size_cd`` bytes of it and steps through
    them 46-byte header by 46-byte header until they run out - the entry count
    is never consulted. Understate the size and zipfile walks less and then
    fails closed on the first header that is not where it says it is;
    overstate it and the walk being described is the walk being refused. So
    ``size_cd // 46`` is a ceiling on the members an open can produce, and
    unlike the count it costs the attacker the bytes it claims.
    """
    start, wide = _end_records(data)
    if start < 0:
        return None
    size = int.from_bytes(data[start + 12:start + 16], "little")
    if size == 0xFFFFFFFF:
        if wide < 0:
            return None
        size = int.from_bytes(data[wide + 40:wide + 48], "little")
    # zipfile can only read what is there, so a wilder claim than that is not
    # a bigger walk, just a broken archive.
    return min(size, len(data))


def _refuse_too_many_members(data: bytes) -> None:
    """Raise before opening an archive whose index is bigger than we allow."""
    declared = declared_members(data)
    if declared is not None and declared > MAX_MEMBERS:
        raise ArchiveTooLarge(f"archive holds {declared} files, more than the {MAX_MEMBERS} allowed")
    index = index_bytes(data)
    if index is not None and index > MAX_MEMBERS * BYTES_PER_MEMBER:
        raise ArchiveTooLarge(f"archive's index describes up to {index // CD_ENTRY_HEADER} files, "
                              f"more than the {MAX_MEMBERS} allowed")


def _refuse_what_was_opened(found: int) -> None:
    """And raise again on the count that was actually read.

    The check before the open is a ceiling, not a measurement, and the two
    end records can disagree with each other. Whatever got through, the exact
    number is known once the index exists and nothing has been parsed yet.
    """
    if found > MAX_MEMBERS:
        raise ArchiveTooLarge(f"archive holds {found} files, more than the {MAX_MEMBERS} allowed")


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
            found = zf.namelist()
    except zipfile.BadZipFile:
        return []
    _refuse_what_was_opened(len(found))
    return found


def json_files(data: bytes, suffix: str = ".json", contains: str = "") -> Iterator[tuple[str, object]]:
    """Yield (path, parsed json) for archive members matching the filters, in path order.

    Refuses archives that declare more members or more uncompressed bytes than
    the caps above, so a zip bomb fails fast instead of filling memory.
    """
    _refuse_too_many_members(data)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        _refuse_what_was_opened(len(infos))
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
