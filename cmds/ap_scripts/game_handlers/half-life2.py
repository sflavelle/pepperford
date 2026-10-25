"""Half-Life 2 — game-specific handling."""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class HL2Handler:
    game = "Half-Life 2"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_location_tracking", "handle_item_tracking"})

    def handle_location_tracking(self, itemlog, player, item, use_everywhere):
        location = item.location.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        game_name = player.game
        
        if location.endswith(" Complete"):
            count = len([l for l in spoiler.values() if l.location.name.endswith(": Complete") and l.found is True])
            required = settings["Chapters Required"]
            return f"{location} ({count}/{required})"


    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        
        if item == "Progressive Gravity Gun":
            tiers = ["Pull/Grab", "Punting", "Supercharged"]
            return f"{item} (tier {count}: {tiers[count-1]})"
        
        if item == "Progressive Aux Power":
            total = 4
            return f"{item} ({count * 25}% Capacity)"


    def handle_state_tracking(self, player, game):
        settings = player.settings
        slot_data = player.slot_data
        spoiler = game.spoiler_log[str(player)]
        player_game = player.game
        goal = ""
        goal_str = ""

        goal = settings["Chapters Required"]
        goal_str = f"Complete {goal} chapters, then retire Doctor Breen in Dark Energy"

        
        player.stats.goal_str = goal_str


handler = HL2Handler()
