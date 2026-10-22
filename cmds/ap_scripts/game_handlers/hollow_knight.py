"""
Hollow Knight — shop/location prices out of slot_data.

The world's own rules (worlds/hk/Rules.py) gate a shop check on the cost terms RANCIDEGGS / GRUBS /
ESSENCE / CHARMS using `state.count(term, player)` - a count of an item *group* - and skip GEO
outright ("No geo logic!"). So:

- Geo is a farmable **cost**: rendered ("800 Geo"), never blocking, never announced.
- The other terms are **group requirements** (`group:Grubs`, ...), counted over the world's own
  `item_name_groups` (worlds/hk/Items.py), which this module carries so a checker does not have to
  load the whole world: 1 grub, 1 rancid egg, 46 charms, 12 boss essences. ESSENCE maps to the
  world's "BossEssence" group.

Group membership was computed from worlds/hk/ExtractedData.py by item type and matches
`worlds.hk.Items.item_name_groups` exactly (checked 2026-10-03 against the built-in world in
/home/hermes/Archipelago/worlds/hk).
"""

from __future__ import annotations

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION
from cmds.ap_scripts.requirements import GROUP_PREFIX, check_requirements

# Subset of the world's item_name_groups that its shop costs can reference.
GROUPS = {
    "Grubs": [
        "Grub"
    ],
    "RancidEggs": [
        "Rancid_Egg"
    ],
    "Charms": [
        "Baldur_Shell",
        "Dashmaster",
        "Deep_Focus",
        "Defender's_Crest",
        "Dream_Wielder",
        "Dreamshield",
        "Flukenest",
        "Fragile_Greed",
        "Fragile_Heart",
        "Fragile_Strength",
        "Fury_of_the_Fallen",
        "Gathering_Swarm",
        "Glowing_Womb",
        "Grimmchild1",
        "Grimmchild2",
        "Grubberfly's_Elegy",
        "Grubsong",
        "Heavy_Blow",
        "Hiveblood",
        "Joni's_Blessing",
        "King_Fragment",
        "Lifeblood_Core",
        "Lifeblood_Heart",
        "Longnail",
        "Mark_of_Pride",
        "Nailmaster's_Glory",
        "Queen_Fragment",
        "Quick_Focus",
        "Quick_Slash",
        "Shaman_Stone",
        "Shape_of_Unn",
        "Sharp_Shadow",
        "Soul_Catcher",
        "Soul_Eater",
        "Spell_Twister",
        "Spore_Shroom",
        "Sprintmaster",
        "Stalwart_Shell",
        "Steady_Body",
        "Thorns_of_Agony",
        "Unbreakable_Greed",
        "Unbreakable_Heart",
        "Unbreakable_Strength",
        "Void_Heart",
        "Wayward_Compass",
        "Weaversong"
    ],
    "BossEssence": [
        "Boss_Essence-Elder_Hu",
        "Boss_Essence-Failed_Champion",
        "Boss_Essence-Galien",
        "Boss_Essence-Gorb",
        "Boss_Essence-Grey_Prince_Zote",
        "Boss_Essence-Lost_Kin",
        "Boss_Essence-Markoth",
        "Boss_Essence-Marmu",
        "Boss_Essence-No_Eyes",
        "Boss_Essence-Soul_Tyrant",
        "Boss_Essence-White_Defender",
        "Boss_Essence-Xero"
    ]
}

# Cost term (Rules.py cost_terms) -> the item group it counts.
COST_TERM_GROUPS = {
    "GRUBS": "Grubs",
    "RANCIDEGGS": "RancidEggs",
    "ESSENCE": "BossEssence",
    "CHARMS": "Charms",
}
GEO_TERM = "GEO"


def group_map():
    """The map to pass as `check_requirements(..., groups=group_map())`."""
    return GROUPS


class HollowKnightHandler:
    game = "Hollow Knight"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_location_hinting", "handle_location_tracking"})

    def handle_location_hinting(self, player, location):
        """Requirements, extra info and costs for one location (protocol v1 shape).

        Returns None when the location has no price, so the default path (nothing) applies.
        """
        slot_data = getattr(player, "slot_data", None) or {}
        prices_by_location = slot_data.get("location_costs") or {}
        name = str(getattr(location, "name", location))
        prices = prices_by_location.get(name)
        if not prices:
            return None

        requirements = {}
        costs = {}
        for term, amount in prices.items():
            key = str(term).upper()
            if key == GEO_TERM:
                costs["Geo"] = amount
            elif key in COST_TERM_GROUPS:
                requirements[f"{GROUP_PREFIX}{COST_TERM_GROUPS[key]}"] = amount
            else:
                # An unknown term: keep it visible, but it cannot be counted.
                requirements[str(term).title()] = amount
        return (requirements, "", costs)


    def handle_location_tracking(self, itemlog, player, item, use_everywhere):
        location = item.location.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        game_name = player.game
        return location.replace('_', ' ').replace('-', ' - ')


    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        if item == 'Grub':
            total = 46
            return f'{item} ({count}/{total})'
        return item.replace('_', ' ').replace('-', ' - ')


handler = HollowKnightHandler()


def _selftest() -> int:
    """`python -m cmds.ap_scripts.game_handlers.hollow_knight` - the price rule and group counting."""
    import types

    h = HollowKnightHandler()
    failures = []

    def player(costs_by_location):
        return types.SimpleNamespace(
            name="HKTest", game="Hollow Knight", id=1, settings={},
            slot_data={"location_costs": costs_by_location}, stats=types.SimpleNamespace(stats={}),
        )

    def loc(name):
        return types.SimpleNamespace(name=name, game="Hollow Knight")

    # Geo only -> cost, no requirement
    req, extra, cost = h.handle_location_hinting(player({"Shop_A": {"GEO": 800}}), loc("Shop_A"))
    if req or cost != {"Geo": 800}:
        failures.append(f"geo should be a cost: {req} / {cost}")

    # grubs -> a group requirement
    req, extra, cost = h.handle_location_hinting(player({"Shop_B": {"GRUBS": 5}}), loc("Shop_B"))
    if req != {"group:Grubs": 5} or cost:
        failures.append(f"grubs should be a group requirement: {req} / {cost}")

    # mixed, and ESSENCE mapped to the world's BossEssence group
    req, extra, cost = h.handle_location_hinting(
        player({"Shop_C": {"RANCIDEGGS": 3, "ESSENCE": 2, "GEO": 30}}), loc("Shop_C")
    )
    if req != {"group:RancidEggs": 3, "group:BossEssence": 2} or cost != {"Geo": 30}:
        failures.append(f"mixed costs wrong: {req} / {cost}")

    # no price -> None (falls through)
    if h.handle_location_hinting(player({}), loc("Shop_A")) is not None:
        failures.append("a location without a price should return None")

    # counting through the group map
    p = types.SimpleNamespace(counts={"Grub": 3, "Baldur_Shell": 2, "Rancid_Egg": 1})
    p.get_item_count = lambda item: p.counts.get(item, 0)
    short = check_requirements(p, {"group:Grubs": 5}, groups=group_map())
    if short.met or short.unmet != {"group:Grubs": 2}:
        failures.append(f"group shortfall wrong: {short}")
    if short.describe() != "still needs 2 Grubs":
        failures.append(f"group description wrong: {short.describe()!r}")
    enough = check_requirements(p, {"group:Grubs": 3}, groups=group_map())
    if not enough.met or enough.describe() != "requirements met: 3 Grubs":
        failures.append(f"met group wrong: {enough}")
    # charms are counted across the whole 46-item group
    charms = check_requirements(p, {"group:Charms": 3}, groups=group_map())
    if charms.met or charms.unmet != {"group:Charms": 1}:
        failures.append(f"charm group count wrong: {charms}")
    # a group we were not given cannot be verified: unknown, with no known shortfall
    gap = check_requirements(p, {"group:Grubs": 5})
    if gap.certain or gap.unknown != ["group:Grubs"]:
        failures.append(f"missing group map should be unknown: {gap}")

    for f in failures:
        print("FAIL:", f)
    print(f"hollow_knight self-test: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
