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


class HereComesNikoHandler:
    game = "Here Comes Niko!"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"should_post_item_send", "on_item_send"})

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

    for f in failures:
        print("FAIL:", f)
    print(f"here_comes_niko announcement self-test: 5 cases, {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
