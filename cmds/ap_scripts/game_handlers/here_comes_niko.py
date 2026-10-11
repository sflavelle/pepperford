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
    HandlerContext,
    PostDecision,
    SendEvent,
)

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
    implements = frozenset({"should_post_item_send"})

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


handler = HereComesNikoHandler()
