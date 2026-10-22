"""
Here Comes Niko! — friend locations post their sends even when the item is filler, provided the
slot's goal is the Friend goal.

Migrated from the inline rule in ap_itemlog.py (`process_new_log_lines`):

    elif (
        Item.location.game == "Here Comes Niko!"
        and Item.location.name in hcn_friends_locations
        and Item.location.player.settings["Completion Goal"] == "Friend"
    ):
        pass

`FRIENDS_LOCATIONS` moved here from `cmds/ap_scripts/utils.py` (which re-exports it as
`hcn_friends_locations` for the spoiler-log code that still uses it) - game data belongs beside the
game handler.
"""

from __future__ import annotations

from cmds.ap_scripts.game_handlers import ClassificationProposal

from cmds.ap_scripts.game_handlers import (
    PROTOCOL_VERSION,
    Announcement,
    HandlerContext,
    PostDecision,
    SendEvent,
)
from cmds.ap_scripts.requirements import check_requirements, normalise

# The 77 friend locations that count under the Friend goal.
FRIENDS_LOCATIONS: list[str] = [
    "Home - Give High Frog Lunchbox",
    "Hairball City - BIG VOLLEY",
    "Hairball City - Dustan on Lighthouse",
    "Hairball City - Gunter on Skyscraper",
    "Hairball City - Nina",
    "Hairball City - Moomy",
    "Hairball City - Fish with Fischer",
    "Hairball City - Game Kid",
    "Hairball City - Blippy Dog",
    "Hairball City - Blippy",
    "Hairball City - Serschel & Louist",
    "Hairball City - Little Gabi's Flowers",
    "Hairball City - Blessley",
    "Hairball City - Mitch",
    "Hairball City - Mai",
    "Turbine Town - Blippy Dog",
    "Turbine Town - Blippy",
    "Turbine Town - Serschel & Louist",
    "Turbine Town - Dustan on Wind Turbine",
    "Turbine Town - Little Gabi's Flowers",
    "Turbine Town - Blessley",
    "Turbine Town - AIR VOLLEY",
    "Turbine Town - Pelly the Engineer",
    "Turbine Town - Fish with Fischer",
    "Turbine Town - Mitch",
    "Turbine Town - Mai",
    "Salmon Creek Forest - Dustan on Mountain",
    "Salmon Creek Forest - Nina",
    "Salmon Creek Forest - Stijn & Melissa",
    "Salmon Creek Forest - Treeman",
    "Salmon Creek Forest - Blessley",
    "Salmon Creek Forest - Little Gabi's Flowers",
    "Salmon Creek Forest - Game Kid",
    "Salmon Creek Forest - Blippy",
    "Salmon Creek Forest - Serschel & Louist",
    "Salmon Creek Forest - Blippy Dog",
    "Salmon Creek Forest - Fish with Fischer",
    "Salmon Creek Forest - SPORTVIVAL",
    "Salmon Creek Forest - Moomy",
    "Salmon Creek Forest - Mitch",
    "Salmon Creek Forest - Mai",
    "Public Pool - Blippy",
    "Public Pool - Frogtective",
    "Public Pool - Blippy Dog",
    "Public Pool - Little Gabi's Flowers",
    "Public Pool - Blessley",
    "Public Pool - SPORTVIVAL VOLLEY",
    "Public Pool - Fish with Fischer",
    "Public Pool - Mitch",
    "Public Pool - Mai",
    "Bathhouse - Poppy",
    "Bathhouse - Fish with Fischer",
    "Bathhouse - Blessley",
    "Bathhouse - Little Gabi's Flowers",
    "Bathhouse - Blippy Dog",
    "Bathhouse - Blippy",
    "Bathhouse - Dustan on Bathhouse",
    "Bathhouse - Game Kid",
    "Bathhouse - LONG VOLLEY",
    "Bathhouse - Nina",
    "Bathhouse - Serschel & Louist",
    "Bathhouse - Moomy",
    "Bathhouse - Mitch",
    "Bathhouse - Mai",
    "Tadpole HQ - Blippy",
    "Tadpole HQ - Little Gabi's Flowers",
    "Tadpole HQ - Blippy Dog",
    "Tadpole HQ - Blessley",
    "Tadpole HQ - Serschel & Louist",
    "Tadpole HQ - Frog King",
    "Tadpole HQ - HUGE VOLLEY",
    "Tadpole HQ - Fish with Fischer",
    "Tadpole HQ - Mitch",
    "Tadpole HQ - Mai",
    "Gary's Garden - Gunter & Little Gabi",
    "Gary's Garden - Mitch",
    "Gary's Garden - Mai",
]

FRIEND_GOAL = "Friend"
# The option that decides which item gates a contact-list location. Source of truth: the world's own
# Options.py (hcniko 0.8.5 in /home/hermes/Archipelago/custom_worlds/):
#
#     class ProgressiveContactList(DefaultOnToggle):
#         """If this option is enabled, the Contact Lists will not be separate, so you cannot get
#         Contact List 2 before Contact List 1."""
#         display_name = "Progressive Contact List"
#
# A DefaultOnToggle, so the world's default is ON: the fallback below has to match that, not "off".
# Items.py gates the two item sets on the same option (`can_create=lambda options: not
# options.progressive_contact_list.value` for the numbered pair), and Rules.py:has_contact_list()
# accepts either (numbered item, or `count` copies of the progressive one).
PROGRESSIVE_CONTACT_LIST_SETTING = "Progressive Contact List"


def option_enabled(settings: dict, name: str, default: bool = False) -> bool:
    """Read a toggle from the slot's settings, tolerating bools, 0/1 and "true"/"false" strings.

    The spoiler-derived settings are display-name -> value, and a toggle can arrive as either a bool
    or its text form, so `bool(settings.get(name))` is not safe: it reads the string "false" as on.
    """
    value = settings.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def contact_list_requirement(which: int, progressive: bool) -> dict[str, int]:
    """The item a contact-list location is gated on.

    With Progressive Contact List off, the world hands out `Contact List 1` and `Contact List 2` as
    separate items. With it on - the world's default - the player receives `Progressive Contact List`
    twice, list 1 first, so a location needing the second list means holding two of them.
    """
    if progressive:
        return {"Progressive Contact List": which}
    return {f"Contact List {which}": 1}


class HereComesNikoHandler:
    game = "Here Comes Niko!"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_location_hinting", "handle_location_tracking", "on_item_send", "resolve_classification", "should_post_item_send"})

    def should_post_item_send(
        self, ctx: HandlerContext, send: SendEvent
    ) -> PostDecision | None:
        goal = ctx.setting("Completion Goal")
        if send.location_name in FRIENDS_LOCATIONS and goal == FRIEND_GOAL:
            return PostDecision(
                post=True,
                reason="friend checks are the goal under the Friend goal",
                dedupe_key=ctx.dedupe_key("hcn-friend", send.location_name),
            )
        return None

    def on_item_send(
        self, ctx: HandlerContext, send: SendEvent
    ) -> list[Announcement] | None:
        """Announce a hinted location in the receiver's world becoming reachable.

        Cassettes (and Coins, and Contact Lists) gate friend checks, and a hint for one of those
        locations can sit unmet for most of a seed. When the item that a location is waiting on
        arrives for the player who owns that location - "<Level> Cassette" per level under Level
        Based cassette logic, "Cassette" otherwise, "Coin" for a kiosk - and the count is now met,
        say so. Nothing else about the send changes; the core renders this in the log channel and
        drops repeats by dedupe_key.
        """
        # The player who gains the item is the one whose own locations may have just opened up.
        receiver = getattr(send.item, "receiver", None)
        if receiver is None or getattr(receiver, "game", None) != self.game:
            return None
        if getattr(receiver, "is_finished", None) and receiver.is_finished():
            return None

        gained = normalise({send.item_name: 1})  # the item this send delivers
        announcements: list[Announcement] = []
        for hint in getattr(receiver, "hints", {}).get("sending", []):
            location = getattr(hint, "location", None)
            requirements = getattr(location, "requirements", None)
            if not location or not requirements:
                continue
            req = normalise(requirements)
            if not any(name in req for name in gained):
                continue  # this location is not waiting on what was just sent
            check = check_requirements(receiver, req)
            if not check.met:
                continue
            announcements.append(
                Announcement(
                    kind="location_checkable",
                    text=(
                        f"**{getattr(receiver, 'name', 'a player')}** can now check "
                        f"**{location.name}** for **{getattr(hint, 'name', 'an item')}** "
                        f"({check.describe()})"
                    ),
                    dedupe_key=ctx.dedupe_key("hcn-checkable", location.name),
                )
            )
        return announcements or None


    def handle_location_tracking(self, itemlog, player, item, use_everywhere=False):
        """Friend locations show their collected count in the location name."""
        location = item.location.name
        settings = player.settings
        spoiler = itemlog.spoiler_log[str(player)]
        hcn_friends_locations = FRIENDS_LOCATIONS

        if (
            location in hcn_friends_locations
            and settings["Completion Goal"] == "Friend"
            and (not use_everywhere)
        ):
            total = len([i for i in spoiler.keys() if i in hcn_friends_locations])
            count = len(
                [
                    i
                    for i in spoiler.values()
                    if i.location.name in hcn_friends_locations and i.found is True
                ]
            )
            return f"{location} (*{count}/{total}*)"


    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        if item == 'Cassette':
            required = max({k: v for k, v in settings.items() if 'Cassette Cost' in k}.values())
            return f'{item} ({count}/{required})'
        if item.endswith('Cassette') and settings['Cassette Logic'] == 'Level Based':
            total = 10
            return f'{item} ({count}/{total})'
        if item == 'Coin':
            required = 76 if settings['Completion Goal'] == 'Employee' else settings['Elevator Cost']
            return f'{item} (*{count}/{required}*)'
        if item in ['Hairball City Bone', 'Turbine Town Bone', 'Salmon Creek Forest Bone', 'Public Pool Bone', 'Bathhouse Bone', 'Tadpole HQ Bone'] and settings['Bonesanity'] == 'Insanity':
            required = 5
            return f'{item} ({count}/{required})'
        if item in ['Hairball City Fish', 'Turbine Town Fish', 'Salmon Creek Forest Fish', 'Public Pool Fish', 'Bathhouse Fish', 'Tadpole HQ Fish'] and settings['Fishsanity'] == 'Insanity':
            required = 5
            return f'{item} ({count}/{required})'
        if item in ['Salmon Creek Forest Seed', 'Hairball City Seed', 'Bathhouse Seed'] and settings['Seedsanity'] == 'Insanity':
            required = 10
            return f'{item} ({count}/{required})'
        if item in ['Hairball City Flower', 'Turbine Town Flower', 'Salmon Creek Forest Flower', 'Public Pool Flower', 'Bathhouse Flower', 'Tadpole HQ Flower'] and settings['Flowersanity'] == 'Insanity':
            level = item.replace(' Flower', '')
            required = len([i for i in spoiler.keys() if i.startswith(f'{level} - ') and 'Flowerbed' in i])
            if required != 0:
                return f'{item} ({count}/{required})'
        if item == "Gary's Garden Seed" and settings['Completion Goal'] == 'Garden':
            total = player.get_item_worldtotal(item)
            return f'{item} (*{count}/{total}*)'


    def handle_location_hinting(self, player, location):
        settings = player.settings
        slot_data = player.slot_data
        requirements = {}
        costs = {}
        extra_info = ""
        l = location
        location = location.name
        def add_requirement(text):
            from cmds.ap_scripts.requirements import parse_requirement_string

            parsed = parse_requirement_string(text)
            if parsed is None:
                requirements[str(text)] = None
            else:
                name, count = parsed
                prev = requirements.get(name)
                requirements[name] = count if prev is None else prev + count
        contact_lists = {'1': [f'Hairball City - {npc}' for npc in ['Mitch', 'Mai', 'Moomy', 'Blippy Dog', 'Nina']] + [f'Turbine Town - {npc}' for npc in ['Mitch', 'Mai', 'Blippy Dog']] + [f'Salmon Creek Forest - {npc}' for npc in ['SPORTVIVAL', 'Mai'] + ['Fish with Fischer', 'Bass', 'Catfish', 'Pike', 'Salmon', 'Trout']], '2': [f'Hairball City - {npc}' for npc in ['Game Kid', 'Blippy', 'Serschel & Louist']] + [f'Turbine Town - {npc}' for npc in ['Blippy', 'Serschel & Louist']] + [f'Salmon Creek Forest - {npc}' for npc in ['Game Kid', 'Blippy', 'Serschel & Louist']] + [f'Public Pool - {npc}' for npc in ['Mitch', 'SPORTVIVAL VOLLEY', 'Blessley'] + ["Little Gabi's Flowers"] + [f'Flowerbed {num + 1}' for num in range(3)]] + [f'Bathhouse - {npc}' for npc in ['Blessley', 'Blippy', 'Blippy Dog'] + ["Little Gabi's Flowers"] + [f'Flowerbed {num + 1}' for num in range(3)] + ['Fish with Fischer', 'Anglerfish', 'Clione', 'Jellyfish', 'Little Wiggly Guy', 'Pufferfish']]}
        level = None
        npc = None
        try:
            level, npc = location.split(' - ')
        except ValueError:
            level = location
            npc = None
        if f'{location} Cassette Cost' in settings:
            cost = settings[f'{location} Cassette Cost']
            cassette_item = f'{level} Cassette' if settings['Cassette Logic'] == 'Level Based' else 'Cassette'
            requirements[cassette_item] = cost
        if f'Kiosk {level} Cost' in settings and location == f'{level} - Kiosk':
            requirements['Coin'] = settings[f'Kiosk {level} Cost']
        progressive_lists = option_enabled(settings, PROGRESSIVE_CONTACT_LIST_SETTING, default=True)
        if location in contact_lists['1']:
            requirements.update(contact_list_requirement(1, progressive_lists))
        if location in contact_lists['2']:
            requirements.update(contact_list_requirement(2, progressive_lists))
        if 'Chatsanity' in location and settings['Textbox'] is True:
            requirements['Textbox'] = None
        return (requirements, extra_info, costs)

    def resolve_classification(self, ctx, item, stored=None):
        setting = item.receiver.settings
        response = stored
        if item.name == "Snail Money" and (
            setting["Enable Achievements"] == "all_achievements" or setting["Snail Shop"] is True
        ):
            response = "progression"
        else:
            response = "filler"
        return ClassificationProposal(classification=response, reason="Snail Money only matters for the Snail Shop/achievements")


handler = HereComesNikoHandler()


def _selftest() -> int:
    """`python -m cmds.ap_scripts.game_handlers.here_comes_niko` — the announcement rule."""
    import types

    from cmds.ap_scripts.game_handlers import HandlerContext, SendEvent

    def player(counts, requirements, game="Here Comes Niko!"):
        location = types.SimpleNamespace(
            name="Salmon Creek Forest - Nina", requirements=requirements, game=game
        )
        hint = types.SimpleNamespace(name="Cassette", location=location)
        p = types.SimpleNamespace(
            name="BeckyNiko",
            game=game,
            hints={"sending": [hint]},
            counts=counts,
            is_finished=lambda: False,
        )
        p.get_item_count = lambda item: p.counts.get(item, 0)
        return p

    def send(p, item_name="Cassette", ctx=None):
        ctx = ctx or HandlerContext(game=game_name_of(p), player=p, seed_name="S", item_index=5)
        item = types.SimpleNamespace(name=item_name, receiver=p, location=None)
        return ctx, SendEvent(ctx=ctx, sender="Someone", receiver=p.name, item=item)

    def game_name_of(p):
        return p.game

    h = HereComesNikoHandler()
    failures = []

    # met, and the send is the item it was waiting on
    ctx, ev = send(player({"Cassette": 50}, {"Cassette": 50}))
    out = h.on_item_send(ctx, ev)
    if not out or len(out) != 1 or "Salmon Creek Forest - Nina" not in out[0].text:
        failures.append(f"expected one announcement, got {out}")

    # still short
    ctx, ev = send(player({"Cassette": 30}, {"Cassette": 50}))
    if h.on_item_send(ctx, ev) is not None:
        failures.append("announced while the requirement is still unmet")

    # not the item this location waits on
    ctx, ev = send(player({"Coin": 999}, {"Cassette": 50}), item_name="Coin")
    if h.on_item_send(ctx, ev) is not None:
        failures.append("announced for an item the location does not require")

    # the player taking the item is not a Niko slot
    ctx, ev = send(player({"Cassette": 50}, {"Cassette": 50}, game="Other Game"))
    if h.on_item_send(ctx, ev) is not None:
        failures.append("announced for a player of a different game")

    # a gate that cannot be counted still shows up in the text
    ctx, ev = send(player({"Cassette": 50}, {"Cassette": 50, "Textbox": None}))
    out = h.on_item_send(ctx, ev)
    if not out or "Textbox" not in out[0].text:
        failures.append(f"uncountable gate not reported: {out}")

    # contact lists: the same gate, named for the progressive option
    from cmds.ap_scripts.requirements import format_requirements

    for which, progressive, expected, expected_text in [
        (1, False, {"Contact List 1": 1}, ["Contact List 1"]),
        (2, False, {"Contact List 2": 1}, ["Contact List 2"]),
        (1, True, {"Progressive Contact List": 1}, ["Progressive Contact List"]),
        (2, True, {"Progressive Contact List": 2}, ["2 Progressive Contact Lists"]),
    ]:
        req = contact_list_requirement(which, progressive)
        if req != expected:
            failures.append(f"contact_list_requirement({which}, {progressive}) = {req} != {expected}")
        if format_requirements(req) != expected_text:
            failures.append(
                f"rendering {req} = {format_requirements(req)} != {expected_text}"
            )

    # reading the option itself: the world's default is ON (DefaultOnToggle), and the text form
    # "false" must not read as on
    for settings, expected in [
        ({}, True),
        ({PROGRESSIVE_CONTACT_LIST_SETTING: False}, False),
        ({PROGRESSIVE_CONTACT_LIST_SETTING: True}, True),
        ({PROGRESSIVE_CONTACT_LIST_SETTING: "False"}, False),
        ({PROGRESSIVE_CONTACT_LIST_SETTING: "false"}, False),
        ({PROGRESSIVE_CONTACT_LIST_SETTING: 0}, False),
        ({PROGRESSIVE_CONTACT_LIST_SETTING: 1}, True),
    ]:
        got = option_enabled(settings, PROGRESSIVE_CONTACT_LIST_SETTING, default=True)
        if got is not expected:
            failures.append(f"option_enabled({settings}) = {got} != {expected}")

    # a progressive list location: the second list needs two of the item
    prog = {"Progressive Contact List": 2}
    ctx, ev = send(player({"Progressive Contact List": 1}, prog),
                   item_name="Progressive Contact List")
    if h.on_item_send(ctx, ev) is not None:
        failures.append("announced the second list with only one progressive list held")
    ctx, ev = send(player({"Progressive Contact List": 2}, prog),
                   item_name="Progressive Contact List")
    out = h.on_item_send(ctx, ev)
    if not out or "2 Progressive Contact Lists" not in out[0].text:
        failures.append(f"progressive list announcement wrong: {out}")
    for f in failures:
        print("FAIL:", f)
    print(f"here_comes_niko self-test: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
