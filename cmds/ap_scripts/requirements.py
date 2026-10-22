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
# resource -> price; only for prices the world reports but does NOT gate on, so they never block
Costs = Mapping[str, int]
# A requirement key may name an item GROUP instead of an item, written `group:<GroupName>`. Worlds
# that gate on a group ("5 Grubs" = any five grub items) need this; the caller supplies the group's
# members because only the world knows them (see game_handlers/hollow_knight.py).
GROUP_PREFIX = "group:"
# Names that are already plural or are mass nouns, so appending an "s" reads wrong ("500 Geos").
INVARIANT_NAMES = frozenset({"Geo"})
# Anything a caller or an old code path may hand over. Mapping (not Dict) so a plain dict[str, int]
# is accepted wherever these are expected.
Requirements = Union[None, Mapping[str, Optional[int]], List[str], str]


def is_group(name: str) -> bool:
    """Is this requirement key an item-group reference?"""
    return str(name).startswith(GROUP_PREFIX)


def group_name(name: str) -> str:
    """The group a `group:<Name>` requirement refers to (the name unchanged if it is not one)."""
    name = str(name)
    return name[len(GROUP_PREFIX):] if name.startswith(GROUP_PREFIX) else name


def display_name(name: str) -> str:
    """The human-facing name for a requirement key - the group marker is an implementation detail."""
    return group_name(name)


def format_one(name: str, count: Optional[int]) -> str:
    """Render a single requirement the way the hints always have.

        ("Cassette", 50)          -> "50 Cassettes"
        ("Hairball City Cassette", 50) -> "50 Hairball City Cassettes"
        ("Contact List 1", 1)     -> "Contact List 1"   (a single item, no count shown)
        ("Textbox", None)         -> "Textbox"          (a gate, never counted)
        ("group:Grubs", 5)        -> "5 Grubs"          (group marker stripped)

    The pre-dict code produced "50 Cassettes" from `f"{cost} Cassettes"` and a bare "Contact List 1",
    so count 1 renders as the name alone: a single key item you either hold or don't.
    """
    name = display_name(name)
    if count is None or count == 1:
        return name
    if name in INVARIANT_NAMES or name.endswith("s"):
        return f"{count} {name}"
    return f"{count} {name}s"


def format_requirements(
    requirements: Requirements, costs: Optional[Mapping[str, int]] = None
) -> List[str]:
    """Render requirements (and optional costs) to the strings the hints and tracker show.

    Requirements come first, then costs, each in declaration order - which is the order the old
    free-text code produced them in (e.g. "50 Cassettes, Contact List 1, 138 Rupees").
    """
    if not requirements:
        rendered: List[str] = []
    elif isinstance(requirements, str):
        rendered = [requirements]
    elif isinstance(requirements, Mapping):
        rendered = [format_one(name, count) for name, count in requirements.items()]
    else:
        rendered = [str(r) for r in requirements]
    if costs:
        rendered.extend(format_one(str(name), int(amount)) for name, amount in costs.items())
    return rendered


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
    """The result of comparing requirements against a player's inventory.

    `met` is about the *gates* only. Costs are prices the world reports but does not gate on (a
    farmable shop price): they never block, and `affordable` says whether the player could pay right
    now, or is None when the resource is not an item we can count.
    """

    met: bool
    unmet: Dict[str, int] = field(default_factory=dict)  # item -> shortfall
    held: Dict[str, int] = field(default_factory=dict)  # item -> how many the player has
    unknown: List[str] = field(default_factory=list)  # gates that cannot be verified
    required: Requirement = field(default_factory=dict)  # the requirement that was checked
    costs: Dict[str, int] = field(default_factory=dict)  # resource -> price
    cost_held: Dict[str, int] = field(default_factory=dict)  # resource -> how many held
    cost_shortfall: Dict[str, int] = field(default_factory=dict)  # resource -> still to pay
    affordable: Optional[bool] = None  # None when the cost resource cannot be counted

    @property
    def certain(self) -> bool:
        """True when nothing about this answer is guesswork."""
        return not self.unknown

    def describe(self) -> str:
        """One line for a log message or an announcement.

            "requirements met: 50 Cassettes, Contact List 1"
            "still needs 12 Cassettes"
            "requirements met: 2 Progressive Contact Lists; also gated by Textbox"
            "requirements met: Contact List 1; costs 138 Rupees (short 98 Rupees)"
        """
        if self.unmet:
            parts = [format_one(name, missing) for name, missing in self.unmet.items()]
            text = "still needs " + ", ".join(parts)
        else:
            # Only what we could actually verify belongs in "met": uncountable gates and groups we
            # had no map for are reported separately below.
            countable = {
                k: v
                for k, v in self.required.items()
                if v is not None and k not in self.unknown
            }
            satisfied = format_requirements(countable)
            text = "requirements met" + (f": {', '.join(satisfied)}" if satisfied else "")
        if self.costs:
            text += "; costs " + ", ".join(
                format_one(name, amount) for name, amount in self.costs.items()
            )
            if self.affordable is False:
                text += " (short " + ", ".join(
                    format_one(name, amount) for name, amount in self.cost_shortfall.items()
                ) + ")"
            elif self.affordable is True:
                text += " (affordable)"
        if self.unknown:
            text += "; also gated by " + ", ".join(display_name(n) for n in self.unknown)
        return text


def _count(player: Any, name: str, held: Optional[Mapping[str, int]]) -> int:
    if held is not None:
        return int(held.get(name, 0))
    try:
        return int(player.get_item_count(name))
    except Exception:
        return 0


def check_requirements(
    player: Any,
    requirements: Requirements,
    costs: Optional[Mapping[str, int]] = None,
    held: Optional[Mapping[str, int]] = None,
    groups: Optional[Mapping[str, Iterable[str]]] = None,
) -> RequirementCheck:
    """Compare gates to a player's inventory, and report costs separately.

    `met` is decided by the gates only. Costs are prices the world *reports* but does not gate on
    (a farmable shop price), so they never block. Pass `held` when you know the resource really is a
    countable item (Niko's Coin is): that fills in `cost_held`/`affordable`. Without it the check
    reports the price and says nothing about whether the player could pay - which is the honest
    answer for a currency the player can also earn in-game.

    `held` also overrides the inventory lookup for gates (tests, or a caller that already counted).
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
        if is_group(name):
            members = (groups or {}).get(group_name(name))
            if not members:
                # The caller did not supply this group, so we cannot count it: report the gap
                # instead of pretending the requirement is unmet.
                unknown.append(name)
                continue
            have = sum(_count(player, member, held) for member in members)
        else:
            have = _count(player, name, held)
        held_out[name] = have
        if have < count:
            unmet[name] = count - have

    price = {str(name): int(amount) for name, amount in (costs or {}).items()}
    cost_held: Dict[str, int] = {}
    cost_shortfall: Dict[str, int] = {}
    affordable: Optional[bool] = None
    if price and held is not None:
        for name, amount in price.items():
            have = int(held.get(name, 0))
            cost_held[name] = have
            if have < amount:
                cost_shortfall[name] = amount - have
        affordable = not cost_shortfall

    return RequirementCheck(
        met=not unmet,
        unmet=unmet,
        held=held_out,
        unknown=unknown,
        required=req,
        costs=price,
        cost_held=cost_held,
        cost_shortfall=cost_shortfall,
        affordable=affordable,
    )


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
        ({"Geo": 500}, ["500 Geo"]),  # a mass noun takes no "s"
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

    p = FakePlayer({"Cassette": 38, "Coin": 100, "Contact List 1": 1})
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

    # costs: informative only - they never block, and affordability needs an explicit `held`
    if format_requirements({"Contact List 1": 1}, {"Rupee": 138}) != [
        "Contact List 1",
        "138 Rupees",
    ]:
        failures.append("formatting requirements + costs together failed")
    priced = check_requirements(p, {"Contact List 1": 1}, costs={"Rupee": 138})
    if not priced.met or priced.affordable is not None:
        failures.append(f"costs should not block or claim affordability: {priced}")
    if priced.describe() != "requirements met: Contact List 1; costs 138 Rupees":
        failures.append(f"cost description wrong: {priced.describe()!r}")
    priced = check_requirements(p, {"Contact List 1": 1}, costs={"Rupee": 138}, held={"Coin": 0, "Rupee": 40})
    if priced.affordable is not False or priced.cost_shortfall != {"Rupee": 98}:
        failures.append(f"cost shortfall wrong: {priced}")
    if "(short 98 Rupees)" not in priced.describe():
        failures.append(f"cost shortfall not described: {priced.describe()!r}")
    priced = check_requirements(p, {"Contact List 1": 1}, costs={"Rupee": 138}, held={"Rupee": 138})
    if priced.affordable is not True or "(affordable)" not in priced.describe():
        failures.append(f"affordable cost not described: {priced}")

    # groups: a key like "group:Grubs" counts the whole group, and renders without the marker
    if format_requirements({"group:Grubs": 5}) != ["5 Grubs"]:
        failures.append("group requirement should render without the marker")
    group_player = FakePlayer({"Grub": 3})
    gcheck = check_requirements(group_player, {"group:Grubs": 5}, groups={"Grubs": ["Grub"]})
    if gcheck.met or gcheck.unmet != {"group:Grubs": 2}:
        failures.append(f"group shortfall wrong: {gcheck}")
    if gcheck.describe() != "still needs 2 Grubs":
        failures.append(f"group description wrong: {gcheck.describe()!r}")
    if not check_requirements(group_player, {"group:Grubs": 3}, groups={"Grubs": ["Grub"]}).met:
        failures.append("a satisfied group should be met")
    # a group we were not given is unknown, not unmet: no known gate is short, but the answer is
    # not certain (the same rule as a None-valued gate)
    unresolved = check_requirements(group_player, {"group:Grubs": 5})
    if unresolved.certain or unresolved.unknown != ["group:Grubs"]:
        failures.append(f"a group with no map must be unknown: {unresolved}")
    if unresolved.describe() != "requirements met; also gated by Grubs":
        failures.append(f"unknown group description wrong: {unresolved.describe()!r}")

    for f in failures:
        print("FAIL:", f)
    print(f"requirements self-test: {len(cases)} render cases + gate/cost checks, "
          f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
