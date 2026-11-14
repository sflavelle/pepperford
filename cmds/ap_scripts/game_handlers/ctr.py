"""Crash Team Racing — game-specific handling."""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class CTRHandler:
    game = "Crash Team Racing"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking"})

    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        
        if item.startswith("Progressive Boost"):
            tiers = ["No Boost", "Regular Boost", "Ultimate Sacred Fire", "Blue Fire"]
            return f'{item} ({tiers[count]})'

handler = CTRHandler()
