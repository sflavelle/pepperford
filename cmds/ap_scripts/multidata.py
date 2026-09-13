"""
Read Archipelago multidata (`.archipelago`) files and derive item classifications from them.

Why this exists
---------------
The item log currently learns an item's classification from a cached database table, which only knows
what it knew when the row was written. A world update (or a settings-dependent item) therefore drifts
silently until someone re-classifies it by hand. The multidata that the Archipelago server itself loads
contains the *whole* seed as generated: every location, the item placed there, who receives it, and that
item's classification flags - plus the per-game datapackage, the generator's real sphere structure, and
region groupings. This module reads it, so the classification can be recorded from the seed itself.

Format (verified against a real 0.6.7 multidata):
    byte 0        format version (decompress accepts <= 3)
    bytes 1..     zlib-compressed, restricted-pickled mapping

The mapping's keys are: slot_data, slot_info, connect_names, locations, checks_in_area, server_options,
er_hint_data, precollected_items, precollected_hints, version, tags, minimum_versions, seed_name,
spheres, datapackage, race_mode.

`locations` is {receiving-agnostic owner slot: {location_id: (item_id, receiving_slot, flags)}} - the item
at that location belongs to the *receiving* slot's game, because item ids are per game.

Unpickling requires Archipelago's `Utils.restricted_loads`, which itself needs `NetUtils` and `Options`
importable. Point ARCHIPELAGO_PATH at the install (default /home/hermes/Archipelago).
"""
from __future__ import annotations

import collections
import os
import pathlib
import sys
import zipfile
import zlib
from typing import Any, Dict, Iterable, Optional, Tuple

# Classification flags as they appear on the wire / in multidata (ItemClassification.as_flag()).
FLAG_FILLER = 0
FLAG_PROGRESSION = 1
FLAG_USEFUL = 2
FLAG_TRAP = 4

FLAG_TO_NAME = {
    FLAG_FILLER: "filler",
    FLAG_PROGRESSION: "progression",
    FLAG_USEFUL: "useful",
    FLAG_TRAP: "trap",
    3: "progression",           # progression | useful
    5: "progression",           # progression | trap
    6: "useful",                # useful | trap
    7: "progression",
}

# pepperford has a vocabulary of its own on top of AP's four wire values. These are *annotations*, so a
# census that disagrees with them is not automatically drift.
CUSTOM_VALUES = {"conditional progression", "currency", "mcguffin"}
# What a census may legitimately show for a row carrying each custom value.
CUSTOM_EQUIVALENTS = {
    "currency": {"filler", "useful"},
    "conditional progression": {"filler", "progression", "useful"},
    "mcguffin": {"progression", "useful"},
}

PERMITTED_VALUES = [
    "progression",
    "conditional progression",
    "useful",
    "currency",
    "filler",
    "trap",
]


def _ap_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("ARCHIPELAGO_PATH", "/home/hermes/Archipelago"))


def load(path: str | pathlib.Path) -> Dict[str, Any]:
    """Load a multidata file (or an AP output zip containing one)."""
    path = pathlib.Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.endswith(".archipelago"):
                    blob = zf.read(name)
                    break
            else:
                raise ValueError(f"no .archipelago member inside {path}")
    else:
        blob = path.read_bytes()

    if not blob:
        raise ValueError(f"empty multidata: {path}")
    fmt = blob[0]
    if fmt > 3:
        raise ValueError(f"multidata format version {fmt} is unsupported (expected <= 3)")

    root = _ap_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from Utils import restricted_loads  # deferred: only needed when actually reading a file

    return restricted_loads(zlib.decompress(blob[1:]))


# ---------------------------------------------------------------- basic accessors

def slots(md: Dict[str, Any]) -> Dict[int, Tuple[str, str]]:
    """{slot: (player_name, game)}"""
    out = {}
    for slot, info in (md.get("slot_info") or {}).items():
        fields = getattr(info, "_asdict", None)
        d = fields() if fields else dict(info)
        out[int(slot)] = (d.get("name"), d.get("game"))
    return out


def datapackage(md: Dict[str, Any]) -> Dict[str, Any]:
    return md.get("datapackage") or {}


def game_checksum(md: Dict[str, Any], game: str) -> Optional[str]:
    return (datapackage(md).get(game) or {}).get("checksum")


def item_names(md: Dict[str, Any], game: str) -> Dict[int, str]:
    """{item_id: name} for one game, from the embedded datapackage."""
    pkg = datapackage(md).get(game) or {}
    return {int(i): n for n, i in (pkg.get("item_name_to_id") or {}).items()}


def location_names(md: Dict[str, Any], game: str) -> Dict[int, str]:
    pkg = datapackage(md).get(game) or {}
    return {int(i): n for n, i in (pkg.get("location_name_to_id") or {}).items()}


def areas(md: Dict[str, Any]) -> Dict[int, Dict[str, list]]:
    """{slot: {area_name: [location_id, ...]}} - the generator's own groupings, for player-facing hints."""
    return {int(slot): dict(table) for slot, table in (md.get("checks_in_area") or {}).items()}


def spheres(md: Dict[str, Any]) -> list:
    """The generator's sphere structure: [{slot: {location_id, ...}}, ...] for spheres 0..n."""
    return md.get("spheres") or []


# ---------------------------------------------------------------- the census

def classification_census(md: Dict[str, Any]) -> Dict[Tuple[str, str], collections.Counter]:
    """
    For every item placed in the seed: how many instances carry each classification.

    Returns {(game, item_name): Counter({flag_value: instance_count})}. The game is the *receiving*
    player's game, because item ids are per game.
    """
    names = {slot: item_names(md, game) for slot, (_name, game) in slots(md).items()}
    slot_game = {slot: game for slot, (_name, game) in slots(md).items()}
    census: Dict[Tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)

    for _owner, table in (md.get("locations") or {}).items():
        for _loc_id, entry in table.items():
            item_id, receiver, flags = int(entry[0]), int(entry[1]), int(entry[2])
            game = slot_game.get(receiver)
            if not game:
                continue
            name = names.get(receiver, {}).get(item_id)
            if name is None:
                name = f"__UNKNOWN_ITEM_{game}_{item_id}__"
            census[(game, name)][flags] += 1
    return dict(census)


def derive(counter: collections.Counter, mixed: str = "conditional progression") -> Tuple[Optional[str], str]:
    """
    Turn one item's flag distribution into a single proposed classification, plus a reason.

    Deliberately conservative: a genuinely mixed item becomes `conditional progression` rather than
    picking a side, because that is what a mixture means for this group's vocabulary.
    """
    if not counter:
        return None, "no instances"
    values = {FLAG_TO_NAME.get(f, f"unknown({f})") for f in counter}
    total = sum(counter.values())
    prog = sum(c for f, c in counter.items() if FLAG_TO_NAME.get(f) == "progression")
    traps = sum(c for f, c in counter.items() if FLAG_TO_NAME.get(f) == "trap")

    if traps and total > traps:
        return None, f"mixed trap + non-trap ({dict(counter)}) - needs a human"
    if len(values) == 1:
        return values.pop(), f"all {total} instance(s) agree"
    if prog:
        return mixed, f"mixed: {prog}/{total} instances progression ({dict(counter)})"
    return None, f"mixed without progression ({dict(counter)}) - needs a human"


# ---------------------------------------------------------------- update policy

def decide(
    current: Optional[str],
    current_checksum: Optional[str],
    census: collections.Counter,
    seed_checksum: Optional[str],
) -> Tuple[Optional[str], str]:
    """
    Decide what (if anything) to write for one (game, item) row.

    The rule keys on *provenance*, not direction:
      * no value at all            -> fill it (the safe subset)
      * world version changed      -> the world speaks: allow either direction, record the new checksum
      * otherwise                  -> only ever raise the value, never lower it; a disagreement is
                                      reported for a human instead of being applied
      * custom values (currency / conditional progression / mcguffin) are annotations, so a census that
        they cover is treated as agreement rather than drift
    """
    derived, why = derive(census)
    if current is None:
        return derived, f"row unclassified -> fill from seed ({why})"
    if current == derived:
        return None, f"already {current}"
    if current in CUSTOM_VALUES:
        if derived in CUSTOM_EQUIVALENTS.get(current, set()) or derived is None:
            return None, f"'{current}' is an annotation covering {derived} - no change"
        return None, f"'{current}' annotation vs census {derived} ({why}) - review by hand"
    if seed_checksum and current_checksum and seed_checksum != current_checksum:
        return derived, (f"world version changed for this game "
                         f"({current_checksum[:8]}.. -> {seed_checksum[:8]}..): "
                         f"{current} -> {derived} ({why})")
    if derived and _rank(derived) > _rank(current):
        return derived, f"upgrade {current} -> {derived} ({why})"
    return None, f"census says {derived}, DB says {current} ({why}) - no automatic downgrade"


_RANK = {"trap": 0, "filler": 1, "currency": 1, "useful": 2, "conditional progression": 3, "progression": 4}


def _rank(value: str) -> int:
    return _RANK.get(value, 0)


def summarise(census: Dict[Tuple[str, str], collections.Counter]) -> Dict[str, int]:
    """Counts of how the census classifies items overall - handy for a dry-run header."""
    out = collections.Counter()
    for counter in census.values():
        value, _why = derive(counter)
        out[value or "unresolved"] += 1
    return dict(out)
