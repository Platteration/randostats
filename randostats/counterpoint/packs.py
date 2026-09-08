"""Loading fact packs and punchline voices from disk.

A pack is a JSON file of sourced statistics; a voice is a JSON file of
sentence templates. Both are plain data, so adding one means dropping a
file in, not touching the engine.

``requires`` on a pack is the seam a paid unlock would use: the app treats
a pack as available unless its id is listed in ``RANDOSTATS_LOCKED``.
Everything shipped here is available.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).parent
CORE_PATH = HERE / "facts.json"
PACK_DIR = HERE / "packs"
VOICE_DIR = HERE / "voices"
DEFAULT_VOICE = "house"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def locked_ids() -> set[str]:
    return {p.strip() for p in os.environ.get("RANDOSTATS_LOCKED", "").split(",") if p.strip()}


@lru_cache(maxsize=1)
def _packs() -> dict[str, dict]:
    core = _read(CORE_PATH)
    packs = {"core": {"id": "core", "name": "Core", "description": "The house set: planet, body, people, space.",
                      "facts": core["facts"], "always_on": True}}
    for path in sorted(PACK_DIR.glob("*.json")):
        data = _read(path)
        packs[data["id"]] = {"id": data["id"], "name": data.get("name", data["id"]),
                             "description": data.get("description", ""), "facts": data["facts"], "always_on": False}
    return packs


@lru_cache(maxsize=1)
def _voices() -> dict[str, dict]:
    """Every voice file, keyed by id. A file without one is named by its path
    rather than raising, so one bad drop-in cannot stop the app starting."""
    voices: dict[str, dict] = {}
    for path in sorted(VOICE_DIR.glob("*.json")):
        try:
            data = _read(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        data["id"] = str(data.get("id") or path.stem)
        voices[data["id"]] = data
    if not voices:
        raise RuntimeError(f"no punchline voices found in {VOICE_DIR}")
    return voices


def list_packs(enabled: set[str] | None = None) -> list[dict]:
    locked = locked_ids()
    out = []
    for pack in _packs().values():
        out.append({"id": pack["id"], "name": pack["name"], "description": pack["description"],
                    "facts": len(pack["facts"]), "always_on": pack["always_on"],
                    "locked": pack["id"] in locked,
                    # A locked pack contributes no facts, so it is not "on".
                    "enabled": pack["always_on"] or (pack["id"] not in locked
                                                     and enabled is not None and pack["id"] in enabled)})
    out.sort(key=lambda p: (not p["always_on"], p["name"]))
    return out


def load_facts(packs: set[str] | None = None) -> list[dict]:
    """Core facts plus the facts of every enabled, unlocked pack, deduped by id."""
    locked = locked_ids()
    chosen = {"core"} | {p for p in (packs or set()) if p not in locked}
    facts: dict[str, dict] = {}
    for pack in _packs().values():
        if pack["id"] not in chosen:
            continue
        for fact in pack["facts"]:
            facts.setdefault(fact["id"], {**fact, "pack": pack["id"]})
    return list(facts.values())


def list_voices() -> list[dict]:
    return [{"id": v["id"], "name": v.get("name", v["id"]), "description": v.get("description", ""),
             "sample": (v.get("percent") or [""])[0]}
            for v in sorted(_voices().values(), key=lambda v: v["id"] != DEFAULT_VOICE)]


def load_voice(voice_id: str = DEFAULT_VOICE) -> dict:
    voices = _voices()
    # The house voice fills any gaps, but must not be assumed to exist: a
    # renamed file should not be a KeyError at startup.
    house = voices.get(DEFAULT_VOICE) or next(iter(voices.values()))
    voice = voices.get(voice_id) or house
    # A voice may leave any list out; the house lines fill the gap.
    return {key: voice.get(key) or house.get(key, []) for key in
            ("percent", "percent_close", "ratio", "fallacy_percent", "fallacy_vague", "fallacy_ratio")
            } | {"id": voice["id"], "name": voice.get("name", voice["id"])}
