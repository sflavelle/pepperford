"""
Location requirements as countable items, so they can be checked against an inventory.

Requirements used to be free text (`"50 Cassettes"`), which meant nothing could compare them to what
a player actually holds: the hint text was the only consumer. They are now a mapping of
**item name -> count**:

    {"Cassette": 50, "Contact List 1": 1}

`format_requirements()` renders that back into the exact strings the hints and the tracker API have
always shown, so the representation can change without changing what a player reads.

A value of `None` marks a gate that is *not* a countable item — a setting or a flag such as
`"Textbox"`, or a Refunct minigame name. Those still render by name, but they can never be verified
against an inventory, and `check_requirements()` says so rather than guessing.

    check_requirements(player, {"Cassette": 50})   # -> RequirementCheck(met=False, unmet={"Cassette": 12}, ...)

The item names are the world's own (`Cassette`, `Hairball City Cassette`, `Coin`), which is what
`Player.get_item_count()` needs — that is the whole point of the change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

# item name -> count; None means "a gate that is not a countable item"
Requirement = Dict[str, Optional[int]]
# Anything a caller or an old code path may hand over. Mapping (not Dict) so a plain dict[str, int]
# is accepted wherever these are expected.
Requirements = Union[None, Mapping[str, Optional[int]], List[str], str]


def format_one(name: str, count: Optional[int]) -> str:
    """Render a single requirement the way the hints always have.

        ("Cassette", 50)          -> "50 Cassettes"
        ("Hairball City Cassette", 50) -> "50 Hairball City Cassettes"
        ("Contact List 1", 1)     -> "Contact List 1"   (a single item, no count shown)
        ("Textbox", None)         -> "Textbox"          (a gate, never counted)

    The pre-dict code produced "50 Cassettes" from `f"{cost} Cassettes"` and a bare "Contact List 1",
    so count 1 renders as the name alone: a single key item you either hold or don't.
    """
    if count is None or count == 1:
        return name
    if name.endswith("s"):
        return f"{count} {name}"
    return f"{count} {name}s"


def format_requirements(requirements: Requirements) -> List[str]:
    """Render requirements to the strings the hints and the tracker API show."""
    if not requirements:
        return []
    if isinstance(requirements, str):
        return [requirements]
    if isinstance(requirements, Mapping):
        return [format_one(name, count) for name, count in requirements.items()]
    return [str(r) for r in requirements]


def parse_requirement_string(text: str) -> Optional[tuple[str, int]]:
    """Best-effort read of a legacy free-text requirement.

        "50 Cassettes" -> ("Cassette", 50)

    Returns None when the string carries no count (e.g. "Textbox"), which is the signal that it is a
    gate rather than a countable item.
    """
    head, _, rest = str(text).partition(" ")
    if not rest or not head.isdigit():
        return None
    name = rest.strip()
    if name.endswith("s") and not name.endswith("ss"):
        name = name[:-1]
    return name, int(head)


def normalise(requirements: Requirements) -> Requirement:
    """Accept anything a handler or an old code path may hand over, return an item->count mapping."""
    if not requirements:
        return {}
    if isinstance(requirements, Mapping):
        return {str(k): (None if v is None else int(v)) for k, v in requirements.items()}
    if isinstance(requirements, str):
        requirements = [requirements]
    out: Requirement = {}
    for entry in requirements:
        parsed = parse_requirement_string(str(entry))
        if parsed is None:
            out[str(entry)] = None  # a gate we cannot count
            continue
        name, count = parsed
        prev = out.get(name)
        out[name] = count if prev is None else prev + count
    return out


@dataclass(frozen=True)
class RequirementCheck:
    """The result of comparing requirements against a player's inventory."""

    met: bool
    unmet: Dict[str, int] = field(default_factory=dict)  # item -> shortfall
    held: Dict[str, int] = field(default_factory=dict)  # item -> how many the player has
    unknown: List[str] = field(default_factory=list)  # gates that cannot be verified
    required: Requirement = field(default_factory=dict)  # the requirement that was checked

    @property
    def certain(self) -> bool:
        """True when nothing about this answer is guesswork."""
        return not self.unknown

    def describe(self) -> str:
        """One line for a log message or an announcement.

            "requirements met: 50 Cassettes, Contact List 1"
            "still needs 12 Cassettes"
            "requirements met: 2 Progressive Contact Lists; also gated by Textbox"
        """
        if self.unmet:
            parts = [format_one(name, missing) for name, missing in self.unmet.items()]
            text = "still needs " + ", ".join(parts)
        else:
            # Only the countable items belong in "met": uncountable gates are reported separately
            # below, so a gate-only requirement describes as just "requirements met".
            countable = {k: v for k, v in self.required.items() if v is not None}
            satisfied = format_requirements(countable)
            text = "requirements met" + (f": {', '.join(satisfied)}" if satisfied else "")
        if self.unknown:
            text += "; also gated by " + ", ".join(self.unknown)
        return text


def check_requirements(
    player: Any, requirements: Requirements, held: Optional[Mapping[str, int]] = None
) -> RequirementCheck:
    """Compare requirements to a player's inventory.

    `held` overrides the inventory lookup (for tests, or for a caller that already counted items).
    Non-item gates (`None` values, or legacy strings with no count) are reported in `unknown`; a
    location with only those is `met` but not `certain`.
    """
    req = normalise(requirements)
    unmet: Dict[str, int] = {}
    held_out: Dict[str, int] = {}
    unknown: List[str] = []

    for name, count in req.items():
        if count is None:
            unknown.append(name)
            continue
        if held is not None:
            have = int(held.get(name, 0))
        else:
            try:
                have = int(player.get_item_count(name))
            except Exception:
                have = 0
        held_out[name] = have
        if have < count:
            unmet[name] = count - have

    return RequirementCheck(met=not unmet, unmet=unmet, held=held_out, unknown=unknown, required=req)


def _selftest() -> int:
    """`python -m cmds.ap_scripts.requirements` — rendering, parsing and the inventory check.

    The rendering cases are the strings the pre-dict code produced (`f"{cost} Cassettes"`,
    `f"{cost} {level} Cassettes"`, `f"{cost} Coins"`, `"Contact List 1"`), so a failure here means the
    hints would change.
    """
    cases = [
        ({"Cassette": 50}, ["50 Cassettes"]),
        ({"Hairball City Cassette": 50}, ["50 Hairball City Cassettes"]),
        ({"Coin": 76}, ["76 Coins"]),
        ({"Contact List 1": 1}, ["Contact List 1"]),
        ({"Contact List 2": 1, "Coin": 30}, ["Contact List 2", "30 Coins"]),
        ({"Textbox": None}, ["Textbox"]),
        ({}, []),
        (["50 Cassettes"], ["50 Cassettes"]),  # legacy list[str]
        (["Textbox"], ["Textbox"]),
    ]
    failures = []
    for req, expected in cases:
        got = format_requirements(req)
        if got != expected:
            failures.append(f"format_requirements({req!r}) = {got} != {expected}")

    if normalise(["50 Cassettes"]) != {"Cassette": 50}:
        failures.append(f"normalise failed on a legacy string: {normalise(['50 Cassettes'])}")
    if normalise(["Textbox"]) != {"Textbox": None}:
        failures.append(f"normalise failed on a non-count gate: {normalise(['Textbox'])}")

    class FakePlayer:
        def __init__(self, counts):
            self.counts = counts

        def get_item_count(self, name):
            return self.counts.get(name, 0)

    p = FakePlayer({"Cassette": 38, "Coin": 100})
    short = check_requirements(p, {"Cassette": 50, "Coin": 76})
    if short.met or short.unmet != {"Cassette": 12}:
        failures.append(f"shortfall wrong: {short}")
    enough = check_requirements(p, {"Cassette": 38, "Coin": 76})
    if not enough.met or not enough.certain:
        failures.append(f"met check wrong: {enough}")
    gate = check_requirements(p, {"Textbox": None})
    if not gate.met or gate.certain or gate.unknown != ["Textbox"]:
        failures.append(f"non-count gate mishandled: {gate}")

    described = check_requirements(p, {"Cassette": 38, "Coin": 76}).describe()
    if described != "requirements met: 38 Cassettes, 76 Coins":
        failures.append(f"met description wrong: {described!r}")
    described = check_requirements(p, {"Cassette": 50}).describe()
    if described != "still needs 12 Cassettes":
        failures.append(f"shortfall description wrong: {described!r}")
    described = check_requirements(p, {"Textbox": None}).describe()
    if described != "requirements met; also gated by Textbox":
        failures.append(f"gate description wrong: {described!r}")

    for f in failures:
        print("FAIL:", f)
    print(f"requirements self-test: {len(cases)} render cases + 6 checks, "
          f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
